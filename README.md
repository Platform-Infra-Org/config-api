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

# Install dependencies (pip.ini points at the internal Artifactory index for tashtiot-apis-library)
pip install -r requirements.txt

# Seed the three governing documents (destructive: clears the collection first)
python seed_config.py

# Run the API (serves on 0.0.0.0:5000)
python -m app.main
```

Swagger UI at `/docs`, metrics at `/metrics` (both provided by the library factory).

## Authentication (optional, env-gated)

Inbound JWT bearer auth is provided by the library's `AuthMiddleware` and wired in via
`general_create_app(enable_auth=True)`. It is **dual-gated**: the capability is compiled in, but the
middleware only registers when the runtime env var `AUTH_ENABLED=true` is set *and* exactly one
verification material is configured. With `AUTH_ENABLED` unset/false (the default) auth is a no-op and
every route is open — fully backward compatible.

When enabled, all coordinate/registry routes under `API_PREFIX` require `Authorization: Bearer <jwt>`;
probe/metrics/openapi paths are always excluded. Misconfiguration (enabled with no material, or more
than one) fails fast at startup with `AuthConfigError`.

Everything is env-configurable (read from the same `.env` by the library's settings) — see
`.env.example`:

| Var | Purpose |
|-----|---------|
| `AUTH_ENABLED` | Runtime master switch |
| `AUTH_PUBLIC_KEY_PEM` / `AUTH_PUBLIC_KEY_PATH` | RS256 offline public key (selects local-pubkey mode) |
| `AUTH_JWKS_URL` / `AUTH_JWKS_CACHE_TTL` | RS256 via a JWKS/OIDC endpoint (selects JWKS mode) |
| `AUTH_HS256_SECRET` | HS256 shared secret (selects HS256 mode) |
| `AUTH_ALGORITHMS` | Allowed signing algorithms (e.g. `["RS256"]`) |
| `AUTH_AUDIENCE` / `AUTH_ISSUER` | Optional `aud`/`iss` claim checks |
| `AUTH_HEADER_NAME` | Header carrying the token (default `Authorization`) |
| `AUTH_EXCLUDE_PATHS` | Extra path prefixes/regexes that bypass auth |

## Endpoints (prefix `/api/v1/infra`, configurable via `API_PREFIX`)

| Method & path | Purpose |
|---------------|---------|
| `GET /projects` | All authorized projects from the registry |
| `GET /config`   | Cascading config resolution — **all** coordinates required (strict 422 if missing) |
| `GET /naming`   | Naming tokens for the given coordinates; with none supplied, the entire naming dictionary |

All routes are read-only `GET`s, so every coordinate binds from **query parameters** via `Depends()`.

## Architecture (`app/v1/config/`)

- **`conf.py`** — `BaseSettings` (Mongo URI/db/collection, poll interval, prefix, title), env-driven.
- **`schemas.py`** — `InfraMetadata` (all-optional) / `RequiredInfraMetadata` (strict), response models,
  and the mutable module-level `LIVE_ALLOWED_*` sets used for validation.
- **`provider.py`** — `MongoConfigProvider`: all Mongo access, `aiocache` (60s TTL), cascading config
  resolution, naming resolution, project registry, and the background allowlist-sync loop.
- **`openapi.py`** — `make_config_openapi`: injects the live allowlists as `enum` values into the
  config/naming query parameters.
- **`app/main.py`** — `create_app()`: builds the Mongo provider, registers the poller via the
  library factory's `async_background_tasks`, includes the router, and installs the OpenAPI patcher.

### Dynamic validation & OpenAPI enums (non-obvious)

The `LIVE_ALLOWED_*` sets are mutable module globals, not static config. A background loop
(`start_periodic_polling`, every `POLL_INTERVAL_SECONDS`) reads the `naming_conventions` and
`project_registry` documents, repopulates the sets in place, and nulls `app.openapi_schema` so the
next schema request regenerates with current enums. Validators are **permissive when a set is empty**
(pre-first-poll / missing document) and for omitted coordinates — keep that guard when editing them.

### The three MongoDB documents (collection `global_configs`, keyed by `doc_type`)

1. `enterprise_configuration` — nested config tree; each level carries a `config` dict, merged
   root → space → network → region → island → environment (deeper overrides shallower).
   `project` is validated but is **not** part of the config cascade path.
2. `naming_conventions` — per-level host/cname token maps.
3. `project_registry` — flat `projects` list of authorized application names.

See `seed_config.py` for the canonical seed data.
