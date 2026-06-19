from typing import List, Optional, Set, Dict, Any
from pydantic import BaseModel, Field, field_validator

# Mutable module-level allowlists, repopulated in place by the background polling
# loop (provider.crawl_and_sync_keys). They drive BOTH Pydantic request validation
# (the field_validators below) AND the OpenAPI enum dropdowns (see openapi.py).
LIVE_ALLOWED_NETWORKS: Set[str] = set()
LIVE_ALLOWED_REGIONS: Set[str] = set()
LIVE_ALLOWED_ISLANDS: Set[str] = set()
LIVE_ALLOWED_ENVIRONMENTS: Set[str] = set()
LIVE_ALLOWED_SPACES: Set[str] = set()
LIVE_ALLOWED_PROJECTS: Set[str] = set()


class InfraMetadata(BaseModel):
    """The environment allocation coordinates layout mapping contract.

    All coordinates are optional. Omitting them on the naming route returns the
    entire naming dictionary; on the config route a missing coordinate simply
    contributes no override layer to the cascade.

    Validators are permissive when the corresponding allowlist is empty (e.g.
    before the first poll, or when the backing document is missing) and for
    omitted (``None``) coordinates. Preserve this guard when editing them.
    """
    space: Optional[str] = Field(None, description="Target organizational data partitioning space name")
    network: Optional[str] = Field(None, description="Target network partition layer name")
    region: Optional[str] = Field(None, description="Target geographical region code")
    island: Optional[str] = Field(None, description="Target logical compute cluster zone")
    environment: Optional[str] = Field(None, description="Target lifecycle deployment tier status")
    project: Optional[str] = Field(None, description="The platform application name submitting the request")

    @field_validator("space")
    @classmethod
    def validate_space(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and LIVE_ALLOWED_SPACES and v not in LIVE_ALLOWED_SPACES:
            raise ValueError(f"Invalid space selection '{v}'. Permitted: {list(LIVE_ALLOWED_SPACES)}")
        return v

    @field_validator("network")
    @classmethod
    def validate_network(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and LIVE_ALLOWED_NETWORKS and v not in LIVE_ALLOWED_NETWORKS:
            raise ValueError(f"Invalid network selection '{v}'. Permitted: {list(LIVE_ALLOWED_NETWORKS)}")
        return v

    @field_validator("region")
    @classmethod
    def validate_region(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and LIVE_ALLOWED_REGIONS and v not in LIVE_ALLOWED_REGIONS:
            raise ValueError(f"Invalid region selection '{v}'. Permitted: {list(LIVE_ALLOWED_REGIONS)}")
        return v

    @field_validator("island")
    @classmethod
    def validate_island(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and LIVE_ALLOWED_ISLANDS and v not in LIVE_ALLOWED_ISLANDS:
            raise ValueError(f"Invalid island selection '{v}'. Permitted: {list(LIVE_ALLOWED_ISLANDS)}")
        return v

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and LIVE_ALLOWED_ENVIRONMENTS and v not in LIVE_ALLOWED_ENVIRONMENTS:
            raise ValueError(f"Invalid environment selection '{v}'. Permitted: {list(LIVE_ALLOWED_ENVIRONMENTS)}")
        return v

    @field_validator("project")
    @classmethod
    def validate_project(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and LIVE_ALLOWED_PROJECTS and v not in LIVE_ALLOWED_PROJECTS:
            raise ValueError(f"Application target project '{v}' not found in registry. Permitted: {list(LIVE_ALLOWED_PROJECTS)}")
        return v


class RequiredInfraMetadata(InfraMetadata):
    """Strict variant where every coordinate is mandatory.

    Used by the config cascade route, which cannot resolve without a full set of
    coordinates. Field validators are inherited; only requiredness is overridden,
    so a missing coordinate yields FastAPI's standard 422 automatically.
    """
    space: str = Field(..., description="Target organizational data partitioning space name")
    network: str = Field(..., description="Target network partition layer name")
    region: str = Field(..., description="Target geographical region code")
    island: str = Field(..., description="Target logical compute cluster zone")
    environment: str = Field(..., description="Target lifecycle deployment tier status")
    project: str = Field(..., description="The platform application name submitting the request")


class ConfigResolutionResponse(BaseModel):
    metadata: InfraMetadata
    configurations: Dict[str, Any]


class NamingConventionResponse(BaseModel):
    metadata: InfraMetadata
    naming_parts: Dict[str, Any] = Field(..., description="Dictionary segment tracking resolved metadata DNS tokens")


class AllProjectsResponse(BaseModel):
    projects: List[str] = Field(..., description="List of all platform application names inside the cluster catalog")
