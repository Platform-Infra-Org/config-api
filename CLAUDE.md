# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A standalone FastAPI service that resolves hierarchical infrastructure **configuration** and
**naming conventions** from MongoDB. Clients pass allocation coordinates
(`space → network → region → island → environment`, plus `project`) as query parameters and
receive resolved config keys or naming tokens via layered inheritance. All routes are read-only `GET`s.

The app is built on the internal `tashtiot-apis-library` factory (`general_create_app`), which
supplies base middleware, `/metrics`, health probes, Swagger UI at `/docs`, and the
`async_background_tasks` hook. This service's *only* responsibility is the Config API — it wires no
ArgoCD/Vault/Git/AWX connectors.

`main.py` opts into the library's inbound JWT auth via `general_create_app(enable_auth=True, ...)`.
The service is protected by **SSO (generic OIDC)**, and this is **pure configuration — no auth code in
this service**. SSO is the library's `JWTVerifier` in JWKS mode: set `AUTH_ENABLED=true` and
`AUTH_OIDC_ISSUER=<issuer>` and the library discovers the provider's JWKS
(`<issuer>/.well-known/openid-configuration` → `jwks_uri`) at startup, verifies inbound tokens against
those keys, and uses the issuer as the expected `iss`. It is **dual-gated**: the middleware only
registers when `AUTH_ENABLED=true` *and* exactly one verification material is configured
(`AUTH_OIDC_ISSUER`/`AUTH_JWKS_URL`, or `AUTH_PUBLIC_KEY_*`, or `AUTH_HS256_SECRET`). With
`AUTH_ENABLED` false (the default) auth is a no-op and the service runs open (backward-compatible).
Misconfiguration (no material, or more than one, or unreachable issuer) fails fast at startup with
`AuthConfigError`.

All auth knobs (incl. the SSO/OIDC ones: `AUTH_OIDC_ISSUER`, `AUTH_OIDC_VERIFY_SSL`,
`AUTH_OIDC_TIMEOUT`, `AUTH_JWKS_URL`, `AUTH_JWKS_CACHE_TTL`, `AUTH_AUDIENCE`, `AUTH_ALGORITHMS`) are
env-driven via the *library's* `ApplicationSettings` singleton — they are **not** part of this
service's `conf.py`. The OIDC-discovery capability itself lives in the library
(`fastapi_template/_internal/security/oidc.py` + `verifier.py`); add general auth features there, not
here. See `.env` and `tests/test_auth.py`.

## Commands

```bash
docker compose up -d                  # start MongoDB (mongo:7.0 on :27017)
pip install -r requirements.txt       # pip.ini points at internal Artifactory for tashtiot-apis-library
python scripts/seed_config.py         # seed the 3 governing docs — DESTRUCTIVE: clears the collection first
python -m app.main                    # run the API
```

A `pytest` suite lives under `tests/` (run `pytest`); it uses a fake Mongo (`tests/fakes.py`) and the
seed shapes mirror `scripts/seed_config.py`. `test_auth.py` exercises the library's auth gating; the rest cover
the Mongo provider, routes, schemas, and OpenAPI enum injection.

## Configuration

Env-driven via `app/v1/config/conf.py` (`BaseSettings`, reads `.env`); see `.env.example`. Key vars:
`MONGO_URI` (carries any auth/TLS credentials), `MONGO_DB_NAME`, the three `MONGO_COLLECTION_*` names
(`_ENTERPRISE_CONFIG`/`_NAMING`/`_PROJECTS`), `POLL_INTERVAL_SECONDS`, `API_PREFIX`, `API_TITLE`.

## Architecture (`app/v1/config/`)

- **`main.py`** (`create_app()`) — builds the `AsyncMongoClient` + `MongoConfigProvider`, includes the
  router, installs the OpenAPI patcher, then appends the poller to `app.state.async_background_tasks`.
- **`provider.py`** (`MongoConfigProvider`) — all Mongo access (one handle per collection: `self.enterprise`
  / `self.naming` / `self.projects`), `aiocache` (60s TTL), config cascade, naming resolution, project
  registry, the coordinate catalog (`get_coordinate_catalog`), and the background allowlist-sync loop. This
  service is the Mongo-backed **origin**; the library only ships a remote HTTP-proxy provider
  (`RemoteConfigProvider`), so this provider stays local.
- **`models.py`** — local write-side Pydantic models (one per collection) for write-time validation in
  `scripts/seed_config.py`. `EnterpriseConfigurationDoc` is fully nested (`SpaceNode → NetworkNode →
  RegionNode → IslandNode → EnvironmentNode`, `extra="forbid"`); per-level `config` payloads stay
  free-form. `CoordinateCatalogResponse` is **not** here — it's consumed from the library's `config_api`.
- **`routes.py`** — `/projects`, `/coordinates` (discovery, sourced from the enterprise config tree;
  200 + empty arrays when unseeded), `/coordinates/tree` (same values as the nested hierarchy via
  `provider.get_coordinate_tree`, typed `CoordinateTreeResponse`), `/config` (strict, 422 if any
  coordinate missing), `/naming` (all optional).

The shared coordinate schemas, response models, the OpenAPI enum patcher (`make_config_openapi`), the
coordinate-validation 422 handler (`install_coordinate_validation_error_handler`), and the `LIVE_ALLOWED_*`
allowlist sets are **not defined here** — they are consumed from the library at
`tashtiot_apis_library.fastapi_template.config_api`. Only origin-specific models live locally in `models.py`.
Do not reintroduce local copies of the shared surface; general capabilities live in the library.

### The three MongoDB collections (one governing document each, with `$jsonSchema` validators)

Split from the former single `global_configs` collection: each `doc_type` is now its own collection
(`enterprise_configuration` / `naming_conventions` / `project_registry`), holding a single document and
created with an envelope-level `$jsonSchema` validator (see `scripts/seed_config.py`). The provider reads
each with `find_one({})`; documents no longer carry a `doc_type` field.

1. `enterprise_configuration` — nested config tree; each level carries a `config` dict, merged
   root → space → network → region → island → environment (**deeper overrides shallower**).
   `project` is validated but is **not** part of the config cascade path.
2. `naming_conventions` — per-level host/cname token maps.
3. `project_registry` — flat `projects` list of authorized application names.

`scripts/seed_config.py` holds the canonical seed data — keep it in sync when changing the document shapes.

### Dynamic validation & OpenAPI enums (non-obvious — read before editing)

The `LIVE_ALLOWED_*` sets (now in the library's `config_api.schemas`) are **mutable module globals**, not
static config. They are the single source of truth for BOTH Pydantic request validation (the library's
`field_validator`s) AND the Swagger enum dropdowns (the library's `make_config_openapi`). This service's
`provider.crawl_and_sync_keys` imports those **same** set objects and a background loop
(`provider.start_periodic_polling`, every `POLL_INTERVAL_SECONDS`) reads the `naming_conventions` and
`project_registry` docs, repopulates the sets **in place** (`.clear()` + `.update()` — never reassign,
or the library would stop seeing the updates), and nulls `app.openapi_schema` so the next schema request
regenerates with current enums.

Two guards that must be preserved when editing validators:
- Validators are **permissive when the allowlist set is empty** (pre-first-poll / missing document).
- Validators are **permissive for omitted (`None`) coordinates**.

### App-wiring subtlety

`general_create_app` wires background tasks at construction time, but the poller needs the `app` to
invalidate its cached OpenAPI schema. `main.py` resolves this with a mutable `app_holder` dict that is
populated immediately after `general_create_app` returns; the poller coroutine only dereferences it
once the lifespan starts. Preserve this pattern if you touch app construction.

### Port / host

`main.py`'s `__main__` block parses `--host`/`--port` via `argparse` (defaults `0.0.0.0:5000`), so the
`Dockerfile` CMD flags are honored. Default port is **5000**, consistent across `main.py`, the README,
and the Dockerfile.
