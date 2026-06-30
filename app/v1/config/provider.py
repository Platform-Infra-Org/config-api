import asyncio
from typing import Any, Dict, List

from pymongo import AsyncMongoClient
from aiocache import Cache
from loguru import logger

from tashtiot_apis_library.fastapi_template.config_api import (
    LIVE_ALLOWED_NETWORKS, LIVE_ALLOWED_REGIONS, LIVE_ALLOWED_ISLANDS,
    LIVE_ALLOWED_ENVIRONMENTS, LIVE_ALLOWED_SPACES, LIVE_ALLOWED_PROJECTS,
    InfraMetadata,
)


class MongoConfigProvider:
    """All MongoDB access for the Config API plus in-memory caching (configurable
    TTL, default 60s) and the background allowlist-sync loop."""

    def __init__(
        self,
        mongo_client: AsyncMongoClient,
        db_name: str,
        *,
        enterprise_collection: str = "enterprise_configuration",
        naming_collection: str = "naming_conventions",
        projects_collection: str = "project_registry",
        cache_ttl_seconds: int = 60,
    ):
        db = mongo_client[db_name]
        # One purpose-built collection per shape, each holding a single document.
        self.enterprise = db[enterprise_collection]
        self.naming = db[naming_collection]
        self.projects = db[projects_collection]
        self._cache = Cache(Cache.MEMORY)
        self._cache_ttl = cache_ttl_seconds

    async def crawl_and_sync_keys(self, app_instance) -> None:
        """Discover allowed coordinate values from Mongo and hot-patch the live
        allowlists, then invalidate the cached Swagger schema so the enum
        dropdowns regenerate on the next request."""
        try:
            # 1. Naming convention coordinate tokens
            naming_doc = await self.naming.find_one({})
            if naming_doc:
                LIVE_ALLOWED_NETWORKS.clear()
                LIVE_ALLOWED_NETWORKS.update(naming_doc.get("network", {}).keys())
                LIVE_ALLOWED_REGIONS.clear()
                LIVE_ALLOWED_REGIONS.update(naming_doc.get("region", {}).keys())
                LIVE_ALLOWED_ISLANDS.clear()
                LIVE_ALLOWED_ISLANDS.update(naming_doc.get("island", {}).keys())
                LIVE_ALLOWED_ENVIRONMENTS.clear()
                LIVE_ALLOWED_ENVIRONMENTS.update(naming_doc.get("environment", {}).keys())
                LIVE_ALLOWED_SPACES.clear()
                LIVE_ALLOWED_SPACES.update(naming_doc.get("space", {}).keys())

            # 2. Global project registry catalog
            project_doc = await self.projects.find_one({})
            if project_doc:
                LIVE_ALLOWED_PROJECTS.clear()
                LIVE_ALLOWED_PROJECTS.update(project_doc.get("projects", []))

            # Invalidate the cached OpenAPI schema so it regenerates with fresh enums.
            app_instance.openapi_schema = None
        except Exception as e:
            logger.error(f"Synchronization pipeline loop operation failure: {e}")

    async def start_periodic_polling(self, app_instance, interval_seconds: int = 5) -> None:
        while True:
            await self.crawl_and_sync_keys(app_instance)
            await asyncio.sleep(interval_seconds)

    async def resolve_infra_config(self, meta: InfraMetadata) -> Dict[str, Any]:
        """Resolve config by merging `config` dicts along the coordinate path,
        root -> space -> network -> region -> island -> environment, where deeper
        layers override shallower ones. `project` is validated but is not part of
        the cascade path."""
        cache_key = f"cfg:{meta.space}:{meta.network}:{meta.region}:{meta.island}:{meta.environment}:{meta.project}"

        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        config_doc = await self.enterprise.find_one({})
        if not config_doc:
            return {}

        layers = []
        layers.append(config_doc.get("config", {}))

        space_node = config_doc.get("space", {}).get(meta.space, {})
        layers.append(space_node.get("config", {}))

        net_node = space_node.get("network", {}).get(meta.network, {})
        layers.append(net_node.get("config", {}))

        reg_node = net_node.get("region", {}).get(meta.region, {})
        layers.append(reg_node.get("config", {}))

        isl_node = reg_node.get("island", {}).get(meta.island, {})
        layers.append(isl_node.get("config", {}))

        env_node = isl_node.get("environment", {}).get(meta.environment, {})
        layers.append(env_node.get("config", {}))

        result = {}
        for layer in layers:
            result.update(layer)

        await self._cache.set(cache_key, result, ttl=self._cache_ttl)
        return result

    async def resolve_naming_convention(self, meta: InfraMetadata) -> Dict[str, Any]:
        """Resolve the naming token suffixes for the supplied coordinates. With no
        coordinates supplied, return the entire naming dictionary."""
        cache_key = f"name:{meta.space}:{meta.network}:{meta.region}:{meta.island}:{meta.environment}:{meta.project}"

        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        naming_doc = await self.naming.find_one({})
        if not naming_doc:
            return {}

        # No metadata coordinates supplied -> return the entire naming dictionary.
        if not any([meta.network, meta.region, meta.island, meta.environment, meta.space]):
            payload = {k: v for k, v in naming_doc.items() if k != "_id"}
            await self._cache.set(cache_key, payload, ttl=self._cache_ttl)
            return payload

        payload = {
            "network": naming_doc.get("network", {}).get(meta.network, {}),
            "region": naming_doc.get("region", {}).get(meta.region, {}),
            "island": naming_doc.get("island", {}).get(meta.island, {}),
            "environment": naming_doc.get("environment", {}).get(meta.environment, {}),
            "space": naming_doc.get("space", {}).get(meta.space, {}),
        }

        await self._cache.set(cache_key, payload, ttl=self._cache_ttl)
        return payload

    async def get_all_projects(self) -> List[str]:
        """Fetch the list of all registered platform project system names."""
        cache_key = "global:project_registry:all_names"

        cached_list = await self._cache.get(cache_key)
        if cached_list is not None:
            return cached_list

        project_doc = await self.projects.find_one({})
        if not project_doc:
            return []

        result_list = project_doc.get("projects", [])
        await self._cache.set(cache_key, result_list, ttl=self._cache_ttl)
        return result_list

    async def get_coordinate_catalog(self) -> Dict[str, List[str]]:
        """Return the valid values for every coordinate level plus the project list.

        Coordinate values are collected by walking the **enterprise configuration
        tree** — the authoritative source of which space → network → region →
        island → environment nodes actually exist — unioning the keys found at each
        depth across every branch. Projects come from the project registry (a
        project is validated but is not part of the cascade tree)."""
        cache_key = "global:coordinate_catalog"

        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        config_doc = await self.enterprise.find_one({}) or {}
        project_doc = await self.projects.find_one({}) or {}

        spaces, networks, regions, islands, environments = set(), set(), set(), set(), set()
        space_map = config_doc.get("space", {})
        spaces.update(space_map.keys())
        for space_node in space_map.values():
            network_map = space_node.get("network", {})
            networks.update(network_map.keys())
            for network_node in network_map.values():
                region_map = network_node.get("region", {})
                regions.update(region_map.keys())
                for region_node in region_map.values():
                    island_map = region_node.get("island", {})
                    islands.update(island_map.keys())
                    for island_node in island_map.values():
                        environments.update(island_node.get("environment", {}).keys())

        catalog = {
            "space": sorted(spaces),
            "network": sorted(networks),
            "region": sorted(regions),
            "island": sorted(islands),
            "environment": sorted(environments),
            "projects": sorted(project_doc.get("projects", [])),
        }

        await self._cache.set(cache_key, catalog, ttl=self._cache_ttl)
        return catalog

    async def get_coordinate_tree(self) -> Dict[str, Any]:
        """Return the coordinate values as a nested hierarchy plus the project list.

        Nested variant of :meth:`get_coordinate_catalog`: instead of unioning keys
        into flat lists, it preserves the **enterprise configuration tree** shape
        (space → network → region → island), with the deepest level being the
        sorted list of environment names. Projects stay flat alongside."""
        cache_key = "global:coordinate_tree"

        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        config_doc = await self.enterprise.find_one({}) or {}
        project_doc = await self.projects.find_one({}) or {}

        tree: Dict[str, Any] = {}
        for space_name, space_node in config_doc.get("space", {}).items():
            networks = tree.setdefault(space_name, {})
            for network_name, network_node in space_node.get("network", {}).items():
                regions = networks.setdefault(network_name, {})
                for region_name, region_node in network_node.get("region", {}).items():
                    islands = regions.setdefault(region_name, {})
                    for island_name, island_node in region_node.get("island", {}).items():
                        islands[island_name] = sorted(island_node.get("environment", {}).keys())

        result = {"coordinates": tree, "projects": sorted(project_doc.get("projects", []))}

        await self._cache.set(cache_key, result, ttl=self._cache_ttl)
        return result
