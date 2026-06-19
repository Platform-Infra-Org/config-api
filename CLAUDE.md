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
It is **dual-gated**: the middleware only registers when the runtime env var `AUTH_ENABLED=true` is
also set (and exactly one verification material is configured), so the default is auth-off /
backward-compatible. All auth knobs (mode/algorithms/audience/issuer/key material/header/excluded
paths) are env-driven via the *library's* `ApplicationSettings` singleton — they are **not** part of
this service's `conf.py`. See `.env.example` and `tests/test_auth.py`.

## Commands

```bash
docker compose up -d                  # start MongoDB (mongo:7.0 on :27017)
pip install -r requirements.txt       # pip.ini points at internal Artifactory for tashtiot-apis-library
python seed_config.py                 # seed the 3 governing docs — DESTRUCTIVE: clears the collection first
python -m app.main                    # run the API
```

`pytest` and coverage plugins are declared in `requirements.txt`, but **no test suite exists yet** —
there is no `tests/` directory. Add tests under a new `tests/` dir if asked.

## Configuration

Env-driven via `app/v1/config/conf.py` (`BaseSettings`, reads `.env`); see `.env.example`. Key vars:
`MONGO_URI`, `MONGO_DB_NAME`, `MONGO_COLLECTION`, `POLL_INTERVAL_SECONDS`, `API_PREFIX`, `API_TITLE`.

## Architecture (`app/v1/config/`)

- **`main.py`** (`create_app()`) — builds the `AsyncMongoClient` + `MongoConfigProvider`, registers the
  poller via the library factory's `async_background_tasks`, includes the router, installs the OpenAPI patcher.
- **`provider.py`** (`MongoConfigProvider`) — all Mongo access, `aiocache` (60s TTL), config cascade,
  naming resolution, project registry, and the background allowlist-sync loop.
- **`schemas.py`** — `InfraMetadata` (all-optional) / `RequiredInfraMetadata` (strict), response models,
  and the mutable module-level `LIVE_ALLOWED_*` sets.
- **`openapi.py`** (`make_config_openapi`) — injects the live allowlists as `enum` values into the
  config/naming query parameters.
- **`routes.py`** — `/projects`, `/config` (strict, 422 if any coordinate missing), `/naming` (all optional).

### The three MongoDB documents (collection `global_configs`, keyed by `doc_type`)

1. `enterprise_configuration` — nested config tree; each level carries a `config` dict, merged
   root → space → network → region → island → environment (**deeper overrides shallower**).
   `project` is validated but is **not** part of the config cascade path.
2. `naming_conventions` — per-level host/cname token maps.
3. `project_registry` — flat `projects` list of authorized application names.

`seed_config.py` holds the canonical seed data — keep it in sync when changing the document shapes.

### Dynamic validation & OpenAPI enums (non-obvious — read before editing)

The `LIVE_ALLOWED_*` sets in `schemas.py` are **mutable module globals**, not static config. They are
the single source of truth for BOTH Pydantic request validation (the `field_validator`s) AND the
Swagger enum dropdowns (`openapi.py`). A background loop (`provider.start_periodic_polling`, every
`POLL_INTERVAL_SECONDS`) reads the `naming_conventions` and `project_registry` docs, repopulates the
sets **in place** (`.clear()` + `.update()` — never reassign), and nulls `app.openapi_schema` so the
next schema request regenerates with current enums.

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
