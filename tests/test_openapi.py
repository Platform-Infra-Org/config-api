"""make_config_openapi injects the live allowlists as enum dropdowns."""
import pytest
from fastapi import FastAPI

from app.v1.config import schemas
from app.v1.config.conf import config
from app.v1.config.openapi import make_config_openapi
from app.v1.config.routes import get_v1_config_router
from tests.conftest import make_provider


CONFIG_PATH = f"{config.API_PREFIX}/config"
NAMING_PATH = f"{config.API_PREFIX}/naming"


@pytest.fixture
def app_with_openapi(seed_docs):
    provider, _ = make_provider(seed_docs)
    app = FastAPI(title="Test API", version="1.0.0")
    app.include_router(get_v1_config_router(provider))
    app.openapi = make_config_openapi(app, config_path=CONFIG_PATH, naming_path=NAMING_PATH)
    return app


def _params_for(schema, path, method="get"):
    return {p["name"]: p for p in schema["paths"][path][method]["parameters"]}


class TestEnumInjection:
    def test_no_enums_when_allowlists_empty(self, app_with_openapi):
        schema = app_with_openapi.openapi()
        params = _params_for(schema, CONFIG_PATH)
        assert "enum" not in params["network"]["schema"]
        assert "enum" not in params["project"]["schema"]

    def test_populated_allowlists_inject_sorted_enums(self, app_with_openapi):
        schemas.LIVE_ALLOWED_NETWORKS.update({"backbone-net", "edge-net"})
        schemas.LIVE_ALLOWED_PROJECTS.update({"payment-gateway", "authentication-service"})
        # Force regeneration (the poller normally nulls this).
        app_with_openapi.openapi_schema = None

        schema = app_with_openapi.openapi()
        config_params = _params_for(schema, CONFIG_PATH)
        assert config_params["network"]["schema"]["enum"] == ["backbone-net", "edge-net"]
        assert config_params["project"]["schema"]["enum"] == sorted(
            ["payment-gateway", "authentication-service"]
        )
        # Enums applied to the naming route too.
        naming_params = _params_for(schema, NAMING_PATH)
        assert naming_params["network"]["schema"]["enum"] == ["backbone-net", "edge-net"]

    def test_schema_is_cached_until_invalidated(self, app_with_openapi):
        first = app_with_openapi.openapi()
        assert app_with_openapi.openapi() is first  # cached identity
        # Changing an allowlist without invalidating does not change the cached schema.
        schemas.LIVE_ALLOWED_REGIONS.update({"us-east"})
        cached = app_with_openapi.openapi()
        assert "enum" not in _params_for(cached, CONFIG_PATH)["region"]["schema"]
        # Invalidation triggers regeneration with the new enum.
        app_with_openapi.openapi_schema = None
        regenerated = app_with_openapi.openapi()
        assert _params_for(regenerated, CONFIG_PATH)["region"]["schema"]["enum"] == ["us-east"]
