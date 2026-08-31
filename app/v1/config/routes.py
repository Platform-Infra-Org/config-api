from fastapi import APIRouter, Depends, HTTPException, Response
from loguru import logger

from .conf import config
from .models import CreateProjectRequest, CreateProjectResponse
from .provider import MongoConfigProvider
from tashtiot_apis_library.fastapi_template.config_api import (
    InfraMetadata, RequiredInfraMetadata,
    ConfigResolutionResponse, NamingConventionResponse, AllProjectsResponse,
    CoordinateCatalogResponse, CoordinateTreeResponse,
)


def get_v1_config_router(provider: MongoConfigProvider) -> APIRouter:
    """Create the APIRouter for the MongoDB-backed infrastructure Config API.

    The read routes bind every coordinate from query parameters via
    ``Depends()``; only project registration takes a request body.
    """
    router = APIRouter(prefix=config.API_PREFIX, tags=config.API_TAGS)

    @router.get("/projects", response_model=AllProjectsResponse, name="List registered projects")
    async def list_registered_platform_projects() -> AllProjectsResponse:
        """Return every authorized project in the active registry catalog."""
        project_list = await provider.get_all_projects()
        if not project_list:
            raise HTTPException(status_code=404, detail="The project inventory catalog is empty.")
        return AllProjectsResponse(projects=project_list)

    @router.post(
        "/projects",
        response_model=CreateProjectResponse,
        status_code=201,
        name="Register a project",
    )
    async def register_platform_project(
        payload: CreateProjectRequest, response: Response,
    ) -> CreateProjectResponse:
        """Add a project to the authorized registry.

        Idempotent: registering a name that already exists is a 200 with no
        change, while a genuine registration is a 201. The project becomes a
        valid ``project`` coordinate once the background poller refreshes the
        allowlist (within ``POLL_INTERVAL_SECONDS``)."""
        if not await provider.add_project(payload.name):
            response.status_code = 200
        return CreateProjectResponse(name=payload.name)

    @router.get("/coordinates", response_model=CoordinateCatalogResponse, name="List allowed coordinate values")
    async def list_coordinate_catalog() -> CoordinateCatalogResponse:
        """Return every valid value per coordinate level plus the project list.

        A discovery endpoint: clients use it to learn which coordinates the
        config/naming routes will accept. Empty arrays (nothing seeded yet) are a
        valid response — no 404."""
        catalog = await provider.get_coordinate_catalog()
        return CoordinateCatalogResponse(**catalog)

    @router.get("/coordinates/tree", response_model=CoordinateTreeResponse, name="List coordinate values as a tree")
    async def list_coordinate_tree() -> CoordinateTreeResponse:
        """Nested variant of ``/coordinates``: the same values shaped as the config
        hierarchy (space → network → region → island → sorted env list), with
        ``projects`` flat alongside. Empty tree (nothing seeded yet) is a valid 200."""
        tree = await provider.get_coordinate_tree()
        return CoordinateTreeResponse(**tree)

    @router.get("/config", response_model=ConfigResolutionResponse, name="Resolve cascading config")
    async def fetch_infrastructure_configurations(
        metadata: RequiredInfraMetadata = Depends(),
    ) -> ConfigResolutionResponse:
        configurations = await provider.resolve_infra_config(metadata)
        if not configurations:
            raise HTTPException(status_code=404, detail="No matching configuration metrics located.")
        return ConfigResolutionResponse(metadata=metadata, configurations=configurations)

    @router.get("/naming", response_model=NamingConventionResponse, name="Resolve naming convention")
    async def fetch_naming_suffixes(
        metadata: InfraMetadata = Depends(),
    ) -> NamingConventionResponse:
        naming_parts = await provider.resolve_naming_convention(metadata)
        if not naming_parts:
            raise HTTPException(status_code=404, detail="Target translation guidelines missing.")
        return NamingConventionResponse(metadata=metadata, naming_parts=naming_parts)

    return router
