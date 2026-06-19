from typing import Callable

from fastapi import FastAPI

from .schemas import (
    LIVE_ALLOWED_NETWORKS, LIVE_ALLOWED_REGIONS, LIVE_ALLOWED_ISLANDS,
    LIVE_ALLOWED_ENVIRONMENTS, LIVE_ALLOWED_SPACES, LIVE_ALLOWED_PROJECTS,
)


def make_config_openapi(app: FastAPI, config_path: str, naming_path: str) -> Callable[[], dict]:
    """Build an ``app.openapi`` replacement that injects the live allowlists as
    ``enum`` values into the coordinate query parameters on the config/naming
    routes.

    This **wraps** whatever ``app.openapi`` is already installed rather than
    rebuilding the schema from scratch, so it composes with the library's other
    OpenAPI customizations — notably the bearer-auth security scheme that
    ``general_create_app`` injects when auth is enabled (the source of Swagger's
    Authorize button). Replacing ``app.openapi`` outright would drop it.

    Title/version/openapi_version come from the underlying generator (set on the
    FastAPI app at construction). The background polling loop nulls
    ``app.openapi_schema`` whenever the allowlists change, so the next schema
    request regenerates — through the same wrapped chain — with current enums.
    """

    # Captured at wire-up time: the generator installed before us (the library's
    # bearer-security wrapper when auth is on, else FastAPI's default).
    base_openapi = app.openapi

    def custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema

        openapi_schema = base_openapi()

        target_paths = [config_path, naming_path]
        http_methods = ("get", "post", "put", "patch", "delete")
        for path in target_paths:
            path_item = openapi_schema.get("paths", {}).get(path, {})
            for method in http_methods:
                for param in path_item.get(method, {}).get("parameters", []):
                    name = param.get("name")
                    if name == "space" and LIVE_ALLOWED_SPACES:
                        param["schema"]["enum"] = sorted(list(LIVE_ALLOWED_SPACES))
                    elif name == "network" and LIVE_ALLOWED_NETWORKS:
                        param["schema"]["enum"] = sorted(list(LIVE_ALLOWED_NETWORKS))
                    elif name == "region" and LIVE_ALLOWED_REGIONS:
                        param["schema"]["enum"] = sorted(list(LIVE_ALLOWED_REGIONS))
                    elif name == "island" and LIVE_ALLOWED_ISLANDS:
                        param["schema"]["enum"] = sorted(list(LIVE_ALLOWED_ISLANDS))
                    elif name == "environment" and LIVE_ALLOWED_ENVIRONMENTS:
                        param["schema"]["enum"] = sorted(list(LIVE_ALLOWED_ENVIRONMENTS))
                    elif name == "project" and LIVE_ALLOWED_PROJECTS:
                        param["schema"]["enum"] = sorted(list(LIVE_ALLOWED_PROJECTS))

        app.openapi_schema = openapi_schema
        return app.openapi_schema

    return custom_openapi
