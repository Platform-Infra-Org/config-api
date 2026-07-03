# infra-config-api

A standalone FastAPI service that resolves hierarchical infrastructure **configuration** and
**naming conventions** from MongoDB. Clients supply allocation coordinates
(`space → network → region → island → environment`, plus `project`) and receive resolved config
keys or naming tokens via layered inheritance.

Built on the internal `tashtiot-apis-library` app factory (`general_create_app`), which supplies
the base middleware, metrics, health probes, and Swagger UI. The Config API is the service's only
responsibility — it wires no ArgoCD/Vault/Git/AWX connectors.

## Local development

```bash
# Start MongoDB
docker compose up -d

# Install dependencies into a managed venv (see "Dependencies with uv" below)
uv sync

# Seed the three governing documents (destructive: clears the collection first)
uv run python scripts/seed_config.py

# Run the API (serves on 0.0.0.0:5000)
uv run python -m app.main
```

Swagger UI at `/docs`, metrics at `/metrics` (both provided by the library factory).

The **app version** shown in the Swagger UI comes from the `APP_VERSION` env var (default `v1.0.0`).
CI should pass the release tag when building the image
(`docker build --build-arg APP_VERSION=<git-tag> .`); the Dockerfile bakes it into `ENV APP_VERSION`.

### Dependencies with uv

This project uses [uv](https://docs.astral.sh/uv/). Dependencies are declared in
`pyproject.toml` and pinned in `uv.lock`; `.python-version` pins the interpreter (3.12). It is an
app, not a library — `[tool.uv] package = false`, so uv manages the venv but never builds or
installs this code as a package.

`tashtiot-apis-library` resolves from a **local editable checkout** at `../apis-library`
(`[tool.uv.sources]`), because the published build lags and is missing `fastapi_template.config_api`.
That sibling checkout must be present for `uv sync` to succeed — on a dev machine and **in the
Docker build** (the image needs the source available, e.g. via a published Artifactory build or a
build context that includes it). All other deps resolve from PyPI.

```bash
uv sync                 # create/update .venv from uv.lock
uv sync --frozen        # install exactly from the lock, never re-resolve (use in CI / images)
uv run <cmd>            # run a command inside the managed venv
uv add <pkg>            # add a runtime dependency (updates pyproject + lock)
uv add --dev <pkg>      # add a dev-only dependency (the `dev` group)
uv lock                 # re-resolve and refresh uv.lock after editing dependencies
uv lock --check         # verify the lock is in sync with pyproject (good CI gate)
```

**Indexes / air-gapped networks.** uv does **not** read `pip.ini`. This project configures no
custom index — `uv.lock` resolves everything from the default PyPI (`pypi.org/simple`). To pull
from an internal mirror instead, add a `[[tool.uv.index]]` block to `pyproject.toml` (or set
`UV_INDEX_URL`) and re-lock. Add `--offline` to use only the uv cache.

Re-locking needs the index; installing from an existing lock (`uv sync --frozen`) does not — so
prefer locking before going air-gapped and only re-lock when dependencies change.

### MongoDB connection & authentication

The service connects using **`MONGO_URI` only** — all credentials and TLS options live in the connection
string, so there is no separate username/password setting. For an authenticated deployment, point
`MONGO_URI` at a URI carrying the credentials, e.g.:

```
mongodb://<user>:<pass>@<host>:27017/infrastructure_governor?authSource=admin&tls=true
mongodb+srv://<user>:<pass>@<cluster>/?retryWrites=true&w=majority   # Atlas / SRV (pymongo[srv])
```

`authSource` is usually `admin`; the database the service reads is set separately by `MONGO_DB_NAME`.
Keep the URI in `.env` (gitignored) or inject it from a secret store — never commit credentials.

## Authentication — SSO (generic OIDC)

The API is protected by **SSO**: clients present a bearer JWT issued by your SSO/OIDC provider, and the
service verifies it **server-side against the provider's published keys (JWKS)** on every request.
Keys are fetched once and cached (`AUTH_JWKS_CACHE_TTL`), so there is no per-request round-trip to the
SSO server. This is the library's `AuthMiddleware` / `JWTVerifier` in JWKS mode — including the **OIDC
discovery** that derives the provider's `jwks_uri` from its issuer — so this service adds no auth code,
only configuration.

**Turn it on with two env vars**: `AUTH_ENABLED=true` and `AUTH_OIDC_ISSUER=<your issuer URL>`. On
startup the library fetches `<issuer>/.well-known/openid-configuration`, locates the `jwks_uri`, and
verifies inbound tokens against it; `AUTH_OIDC_ISSUER` also becomes the expected `iss`. With
`AUTH_ENABLED` false (the default) auth is a no-op and every route is open — backward compatible.

When enabled, all coordinate/registry routes under `API_PREFIX` require `Authorization: Bearer <jwt>`;
probe/metrics/openapi paths are always excluded. A broken issuer (discovery fails or returns no
`jwks_uri`), or more than one verification material, fails fast at startup with `AuthConfigError`.

Everything is env-configurable (read from the same `.env` by the library's settings):

| Var | Purpose |
|-----|---------|
| `AUTH_ENABLED` | Runtime master switch |
| `AUTH_OIDC_ISSUER` | SSO issuer URL. The library discovers its JWKS and enforces it as `iss` |
| `AUTH_AUDIENCE` | Expected `aud` claim (recommended in production; unset skips the check) |
| `AUTH_JWKS_URL` | Explicit JWKS endpoint override; unset triggers discovery from the issuer |
| `AUTH_JWKS_CACHE_TTL` | Lifetime (s) for cached JWKS keys (default `3600`) |
| `AUTH_OIDC_VERIFY_SSL` / `AUTH_OIDC_TIMEOUT` | TLS verification / timeout for the discovery request |
| `AUTH_ALGORITHMS` | Accepted signing algorithms (default `["RS256"]`) |
| `AUTH_HEADER_NAME` | Header carrying the token (default `Authorization`) |
| `AUTH_EXCLUDE_PATHS` | Extra path prefixes/regexes that bypass auth |

The manual materials `AUTH_PUBLIC_KEY_PEM` / `AUTH_PUBLIC_KEY_PATH` (offline RS256) and
`AUTH_HS256_SECRET` (shared secret) remain available for non-SSO setups, but are mutually exclusive
with `AUTH_OIDC_ISSUER` / `AUTH_JWKS_URL`.

## Endpoints (prefix `/api/v1/infra`, configurable via `API_PREFIX`)

| Method & path | Purpose |
|---------------|---------|
| `GET /projects` | All authorized projects from the registry |
| `GET /coordinates` | Discovery: valid values per coordinate level (`space`/`network`/`region`/`island`/`environment`) collected from the **enterprise config tree**, plus `projects` from the registry (200 with empty arrays when unseeded) |
| `GET /coordinates/tree` | Same discovery values shaped as the nested config hierarchy (`coordinates`: space → network → region → island → sorted env list), plus flat `projects` (200 with empty tree when unseeded) |
| `GET /config`   | Cascading config resolution — **all** coordinates required (strict 422 if missing) |
| `GET /naming`   | Naming tokens for the given coordinates; with none supplied, the entire naming dictionary |

All routes are read-only `GET`s, so every coordinate binds from **query parameters** via `Depends()`.

## Architecture (`app/v1/config/`)

- **`conf.py`** — `BaseSettings` (Mongo URI/db, the three `MONGO_COLLECTION_*` names, poll interval,
  prefix, title), env-driven.
- **`models.py`** — local write-side Pydantic models, one per collection, used to validate writes in
  `scripts/seed_config.py`. `EnterpriseConfigurationDoc` is **fully nested** (typed `SpaceNode → NetworkNode
  → RegionNode → IslandNode → EnvironmentNode`, `extra="forbid"`); per-level `config` payloads and the
  naming values stay free-form by design. The shared coordinate/response contract — including
  `CoordinateCatalogResponse` for `/coordinates` and `CoordinateTreeResponse` for `/coordinates/tree`
  — comes from the library's `config_api`.
- **`provider.py`** — `MongoConfigProvider`: all Mongo access (one handle per collection), `aiocache`
  (60s TTL), cascading config resolution, naming resolution, project registry, the coordinate catalog, and
  the background allowlist-sync loop. This service is the Mongo-backed **origin**; the library only ships a
  remote HTTP-proxy provider, so this stays local.
- **`app/main.py`** — `create_app()`: builds the Mongo provider, includes the router, installs the
  coordinate 422 handler and the OpenAPI patcher, then appends the poller to
  `app.state.async_background_tasks` (the registry the library factory's lifespan launches at startup).

The coordinate schemas (`InfraMetadata` / `RequiredInfraMetadata`), response models, the OpenAPI enum
patcher (`make_config_openapi`), the coordinate-validation 422 handler, and the `LIVE_ALLOWED_*` sets are
**consumed from the library** at `tashtiot_apis_library.fastapi_template.config_api` — not defined here.

### Dynamic validation & OpenAPI enums (non-obvious)

The `LIVE_ALLOWED_*` sets (in the library's `config_api`) are mutable module globals, not static config.
This service's `provider.crawl_and_sync_keys` imports those **same** set objects, and a background loop
(`start_periodic_polling`, every `POLL_INTERVAL_SECONDS`) reads the `naming_conventions` and
`project_registry` documents, repopulates the sets **in place** (`.clear()` + `.update()` — never
reassign, or the library would stop seeing the updates), and nulls `app.openapi_schema` so the next
schema request regenerates with current enums. The library's validators are **permissive when a set is
empty** (pre-first-poll / missing document) and for omitted coordinates — keep that guard when editing.

### The three MongoDB collections (one governing document each)

1. `enterprise_configuration` — nested config tree; each level carries a `config` dict, merged
   root → space → network → region → island → environment (deeper overrides shallower).
   `project` is validated but is **not** part of the config cascade path.
2. `naming_conventions` — per-level host/cname token maps.
3. `project_registry` — flat `projects` list of authorized application names.

Each collection is created with a `$jsonSchema` validator (envelope-level enforcement on write), and
`scripts/seed_config.py` validates every document through its `app/v1/config/models.py` Pydantic model
before inserting. Reads in `provider.py` stay dict-based/tolerant so a slightly-off document never 500s
the read path. See `scripts/seed_config.py` for the canonical seed data and validators.

> Note: the docs reference `docker compose up -d`, but the repo currently has **no compose file** — run
> Mongo however you prefer (e.g. `docker run -p 27017:27017 mongo:7.0`).
