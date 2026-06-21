# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-06-21

Initial release of the infrastructure Config API — a read-only FastAPI service that
resolves hierarchical configuration and naming conventions from MongoDB.

### Added

- **Config cascade** — `GET /config` resolves configuration by merging `config`
  layers along `space → network → region → island → environment` (deeper overrides
  shallower). All coordinates are required; a missing one returns `422`.
- **Naming resolution** — `GET /naming` returns per-level host/cname tokens for the
  supplied coordinates, or the entire naming dictionary when none are given.
- **Project registry** — `GET /projects` lists every authorized application name.
- **Coordinate discovery** — `GET /coordinates` returns the valid values per
  coordinate level (collected from the enterprise config tree) plus the project list,
  so clients can discover what the other routes will accept.
- **MongoDB storage** — three purpose-built collections (`enterprise_configuration`,
  `naming_conventions`, `project_registry`), each created with a `$jsonSchema`
  validator and seeded by `scripts/seed_config.py` (validated through Pydantic models
  before write). Connection — including authentication and TLS — is driven entirely by
  `MONGO_URI`.
- **Dynamic validation & OpenAPI enums** — a background poller syncs the live
  coordinate/project allowlists from MongoDB, hot-patching both the request validators
  and the Swagger `enum` dropdowns.
- **SSO (generic OIDC)** — optional inbound JWT auth provided by
  `tashtiot-apis-library`; enabled by configuration only (`AUTH_ENABLED`,
  `AUTH_OIDC_ISSUER`, …), off by default for backward compatibility.
- **Shared Config API primitives** consumed from
  `tashtiot_apis_library.fastapi_template.config_api` (coordinate schemas, response
  models, the OpenAPI enum patcher, the coordinate-validation `422` handler, and the
  `CoordinateCatalogResponse`); only the Mongo-backed provider and write-side document
  models are local.
- Test suite under `tests/` and an `.env.example` template.

[1.0.0]: https://github.com/AdelinMist/config-api/releases/tag/v1.0.0
