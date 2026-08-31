"""Local document + response models for the MongoDB-backed Config API origin.

The shared coordinate/response contract (``InfraMetadata`` etc.) lives in the
library at ``tashtiot_apis_library.fastapi_template.config_api``. These models are
origin-specific:

- The ``*Doc`` models describe the shape of each governing collection. Enterprise
  configuration is modelled **fully** — every level of the
  ``space → network → region → island → environment`` hierarchy is a typed node
  (``extra="forbid"`` so stray/typo'd keys are rejected). The per-level ``config``
  payloads stay ``Dict[str, Any]`` by design — those are intentionally free-form
  key/value config. Naming values stay ``Dict[str, Any]`` because they are
  heterogeneous (naming ``space`` values are bare strings while other levels are
  ``{host, cname}`` dicts). Enforcement happens at the **write boundary**
  (``scripts/seed_config.py``) and via per-collection ``$jsonSchema`` validators;
  provider reads stay dict-based / tolerant so a slightly-off document never 500s
  the read path.

The shared response/coordinate contract — including ``CoordinateCatalogResponse``
for the ``/coordinates`` route — lives in the library at
``tashtiot_apis_library.fastapi_template.config_api``; only these write-side
document models are origin-specific.
"""
from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field


class _HierarchyNode(BaseModel):
    """Base for every cascade node: forbids unknown keys so a mistyped level or
    config key is rejected at the write boundary rather than silently ignored."""

    model_config = ConfigDict(extra="forbid")


class EnvironmentNode(_HierarchyNode):
    """Leaf of the cascade: a lifecycle tier (e.g. ``staging``/``production``)."""

    config: Dict[str, Any] = Field(default_factory=dict)


class IslandNode(_HierarchyNode):
    """A logical compute cluster zone, keyed children by environment name."""

    config: Dict[str, Any] = Field(default_factory=dict)
    environment: Dict[str, EnvironmentNode] = Field(default_factory=dict)


class RegionNode(_HierarchyNode):
    """A geographical region, keyed children by island name."""

    config: Dict[str, Any] = Field(default_factory=dict)
    island: Dict[str, IslandNode] = Field(default_factory=dict)


class NetworkNode(_HierarchyNode):
    """A network partition layer, keyed children by region code."""

    config: Dict[str, Any] = Field(default_factory=dict)
    region: Dict[str, RegionNode] = Field(default_factory=dict)


class SpaceNode(_HierarchyNode):
    """An organizational data-partitioning space, keyed children by network name."""

    config: Dict[str, Any] = Field(default_factory=dict)
    network: Dict[str, NetworkNode] = Field(default_factory=dict)


class EnterpriseConfigurationDoc(_HierarchyNode):
    """The cascading configuration tree (collection ``enterprise_configuration``).

    Root carries global ``config`` plus the ``space`` map; each space drills down
    network → region → island → environment, every node merged shallow→deep by
    ``MongoConfigProvider.resolve_infra_config``."""

    config: Dict[str, Any] = Field(default_factory=dict)
    space: Dict[str, SpaceNode] = Field(default_factory=dict)


class NamingConventionsDoc(BaseModel):
    """Per-level host/cname token maps (collection ``naming_conventions``)."""

    network: Dict[str, Any] = Field(default_factory=dict)
    region: Dict[str, Any] = Field(default_factory=dict)
    island: Dict[str, Any] = Field(default_factory=dict)
    environment: Dict[str, Any] = Field(default_factory=dict)
    space: Dict[str, Any] = Field(default_factory=dict)


class ProjectRegistryDoc(BaseModel):
    """Flat catalog of authorized application names (collection ``project_registry``)."""

    projects: List[str] = Field(default_factory=list)


class CreateProjectRequest(BaseModel):
    """Body of ``POST /projects``. Project names are a write boundary: they end up
    in the registry allowlist and downstream in host/cname naming, so the slug
    pattern is enforced here rather than trusted from the caller."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$",
        min_length=2,
        max_length=64,
        description="Kebab-case project system name, e.g. 'platform'.",
        examples=["platform"],
    )


class CreateProjectResponse(BaseModel):
    """Echo of the registered project name. The status code carries the outcome:
    201 when newly registered, 200 when it was already in the registry."""

    name: str
