import argparse

from fastapi import FastAPI
import uvicorn
from pymongo import AsyncMongoClient
from tashtiot_apis_library import general_create_app

from .v1.config.conf import config as config_v1_config
from .v1.config.errors import install_coordinate_validation_error_handler
from .v1.config.provider import MongoConfigProvider
from .v1.config.routes import get_v1_config_router
from .v1.config.openapi import make_config_openapi


def create_app() -> FastAPI:
    # MongoDB-backed Config API. The client connects lazily, so app creation does
    # not require a live Mongo; the provider's background loop syncs the OpenAPI
    # enum allowlists once the connection is available.
    mongo_client = AsyncMongoClient(config_v1_config.MONGO_URI)
    config_provider = MongoConfigProvider(
        mongo_client,
        db_name=config_v1_config.MONGO_DB_NAME,
        collection_name=config_v1_config.MONGO_COLLECTION,
    )

    # The polling loop needs the app to invalidate its cached OpenAPI schema, but
    # general_create_app wires background tasks at construction time. Resolve the
    # app lazily through a holder — the coroutine only runs once lifespan starts,
    # after the holder has been populated below.
    app_holder = {}

    async def _poll_config() -> None:
        await config_provider.start_periodic_polling(
            app_instance=app_holder["app"],
            interval_seconds=config_v1_config.POLL_INTERVAL_SECONDS,
        )

    # enable_auth declares the capability at the code level; the library's
    # AuthMiddleware is dual-gated, so it only actually registers when the
    # AUTH_ENABLED env var is also true (and exactly one verification material is
    # configured). With AUTH_ENABLED unset/false — the default — auth is a no-op
    # and the service behaves exactly as before. All auth knobs (mode/algorithms/
    # audience/issuer/key material/excluded paths) are env-driven via the
    # library's settings; see .env.example.
    app = general_create_app(
        enable_auth=True,
        async_background_tasks=[_poll_config],
        title=config_v1_config.API_TITLE,
        version="1.0.0",
    )
    app_holder["app"] = app

    # Single responsibility: the infrastructure Config API.
    app.include_router(get_v1_config_router(config_provider))

    # A coordinate rejected by a field_validator (e.g. outside its allowlist) is
    # raised during Depends() model construction and would otherwise 500; map it
    # to the standard 422 validation response.
    install_coordinate_validation_error_handler(app)

    # Install the dynamic OpenAPI enum hot-patcher for the config/naming routes.
    # It wraps the existing app.openapi (incl. the library's bearer-auth security
    # scheme when auth is enabled), so the Swagger Authorize tab is preserved.
    app.openapi = make_config_openapi(
        app,
        config_path=f"{config_v1_config.API_PREFIX}/config",
        naming_path=f"{config_v1_config.API_PREFIX}/naming",
    )

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the infrastructure Config API.")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0).")
    parser.add_argument("--port", type=int, default=5000, help="Bind port (default: 5000).")
    args = parser.parse_args()

    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port)
