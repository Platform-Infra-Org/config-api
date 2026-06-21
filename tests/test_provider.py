"""MongoConfigProvider: config cascade, naming resolution, registry, polling."""
import pytest

from tashtiot_apis_library.fastapi_template import config_api as schemas
from tashtiot_apis_library.fastapi_template.config_api import InfraMetadata
from tests.conftest import make_provider
from tests.fakes import FakeApp


def _full_meta(**overrides):
    base = dict(
        space="core-infrastructure", network="backbone-net", region="us-east",
        island="compute-island-a", environment="production", project="payment-gateway",
    )
    base.update(overrides)
    return InfraMetadata(**base)


class TestResolveInfraConfig:
    async def test_full_path_merges_all_layers_deeper_overrides_shallower(self, provider):
        result = await provider.resolve_infra_config(_full_meta(environment="production"))
        # Root layer.
        assert result["global_timeout_ms"] == 3000
        assert result["monitoring_provider"] == "datadog"
        # Space / network / region layers.
        assert result["space_policy_class"] == "tier-1-governed"
        assert result["ntp_server"] == "pool.ntp.org"
        assert result["aws_vpc_id"] == "vpc-0a1b2c3d"
        # island sets cluster_size=5, production overrides to 20 (deeper wins).
        assert result["cluster_size"] == 20
        assert result["debug_mode"] is False

    async def test_shallower_value_survives_when_deeper_layer_is_empty(self, provider):
        # staging's config is {} so the island's cluster_size=5 is not overridden.
        result = await provider.resolve_infra_config(_full_meta(environment="staging"))
        assert result["cluster_size"] == 5
        assert "debug_mode" not in result

    async def test_partial_coordinates_contribute_only_present_layers(self, provider):
        # Only space supplied: root + space config, nothing deeper.
        meta = InfraMetadata(space="core-infrastructure")
        result = await provider.resolve_infra_config(meta)
        assert result["global_timeout_ms"] == 3000
        assert result["space_policy_class"] == "tier-1-governed"
        assert "ntp_server" not in result

    async def test_unknown_coordinates_yield_only_root(self, provider):
        meta = InfraMetadata(space="does-not-exist")
        result = await provider.resolve_infra_config(meta)
        assert result == {"global_timeout_ms": 3000, "monitoring_provider": "datadog"}

    async def test_missing_config_document_returns_empty(self, empty_provider):
        assert await empty_provider.resolve_infra_config(_full_meta()) == {}

    async def test_result_is_cached_second_call_skips_mongo(self, provider):
        meta = _full_meta()
        first = await provider.resolve_infra_config(meta)
        hits_after_first = provider._fake_collection.find_one_calls
        second = await provider.resolve_infra_config(meta)
        assert second == first
        # No additional find_one issued on the cache hit.
        assert provider._fake_collection.find_one_calls == hits_after_first


class TestResolveNamingConvention:
    async def test_no_coordinates_returns_entire_dictionary(self, provider):
        payload = await provider.resolve_naming_convention(InfraMetadata())
        assert set(payload.keys()) == {"network", "region", "island", "environment", "space"}
        assert "_id" not in payload and "doc_type" not in payload
        assert payload["space"]["tenant-alpha"] == "alpha.tenant.com"

    async def test_coordinates_resolve_token_maps(self, provider):
        meta = InfraMetadata(network="backbone-net", region="us-east", environment="production")
        payload = await provider.resolve_naming_convention(meta)
        assert payload["network"] == {"host": "bb", "cname": "net"}
        assert payload["region"] == {"host": "use1", "cname": "east"}
        assert payload["environment"] == {"host": "prd", "cname": "prod"}
        # Unsupplied coordinates resolve to empty maps.
        assert payload["island"] == {}
        assert payload["space"] == {}

    async def test_unknown_coordinate_resolves_to_empty_map(self, provider):
        payload = await provider.resolve_naming_convention(InfraMetadata(network="ghost-net"))
        assert payload["network"] == {}

    async def test_missing_naming_document_returns_empty(self, empty_provider):
        assert await empty_provider.resolve_naming_convention(InfraMetadata()) == {}


class TestGetAllProjects:
    async def test_returns_registry_list(self, provider):
        projects = await provider.get_all_projects()
        assert projects == [
            "payment-gateway", "authentication-service",
            "notification-engine", "data-warehouse-pipeline",
        ]

    async def test_missing_registry_returns_empty_list(self, empty_provider):
        assert await empty_provider.get_all_projects() == []

    async def test_cached_after_first_fetch(self, provider):
        await provider.get_all_projects()
        hits = provider._fake_collection.find_one_calls
        await provider.get_all_projects()
        assert provider._fake_collection.find_one_calls == hits


class TestCrawlAndSyncKeys:
    async def test_populates_allowlists_in_place_and_invalidates_schema(self, provider):
        # Capture identities to prove sets are mutated in place, never reassigned.
        net_set_id = id(schemas.LIVE_ALLOWED_NETWORKS)
        proj_set_id = id(schemas.LIVE_ALLOWED_PROJECTS)
        app = FakeApp()

        await provider.crawl_and_sync_keys(app)

        assert schemas.LIVE_ALLOWED_NETWORKS == {"backbone-net"}
        assert schemas.LIVE_ALLOWED_REGIONS == {"us-east"}
        assert schemas.LIVE_ALLOWED_ISLANDS == {"compute-island-a"}
        assert schemas.LIVE_ALLOWED_ENVIRONMENTS == {"staging", "production"}
        assert schemas.LIVE_ALLOWED_SPACES == {"core-infrastructure", "tenant-alpha"}
        assert schemas.LIVE_ALLOWED_PROJECTS == {
            "payment-gateway", "authentication-service",
            "notification-engine", "data-warehouse-pipeline",
        }
        # In-place mutation, not reassignment.
        assert id(schemas.LIVE_ALLOWED_NETWORKS) == net_set_id
        assert id(schemas.LIVE_ALLOWED_PROJECTS) == proj_set_id
        # Cached OpenAPI schema invalidated so enums regenerate.
        assert app.openapi_schema is None

    async def test_missing_documents_leave_allowlists_untouched(self, empty_provider):
        schemas.LIVE_ALLOWED_NETWORKS.update({"preexisting"})
        app = FakeApp()
        await empty_provider.crawl_and_sync_keys(app)
        # No naming/registry docs -> sets unchanged, but schema still invalidated.
        assert schemas.LIVE_ALLOWED_NETWORKS == {"preexisting"}
        assert app.openapi_schema is None

    async def test_exception_is_swallowed(self, monkeypatch):
        # crawl must never crash the polling loop; errors are logged, not raised.
        prov, _ = make_provider([])

        async def boom(_query):
            raise RuntimeError("mongo down")

        monkeypatch.setattr(prov.collection, "find_one", boom)
        app = FakeApp()
        await prov.crawl_and_sync_keys(app)  # should not raise
        # Failed before reaching the invalidation line.
        assert app.openapi_schema == "stale-cached-schema"
