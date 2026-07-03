from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, StrictStr
from typing import List


class ConfigV1Settings(BaseSettings):
    """Settings for the v1 infrastructure Config API (MongoDB-backed)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    API_TITLE: str = Field(
        default="Infrastructure Config API",
        description="API title shown in the Swagger UI.",
    )

    APP_VERSION: str = Field(
        default="v1.0.0",
        description="Release version shown in the Swagger UI; injected at image build time from the git tag (see README).",
    )

    MONGO_URI: str = Field(
        default="mongodb://localhost:27017",
        description="Connection URI for the MongoDB instance backing the Config API.",
    )

    MONGO_DB_NAME: str = Field(
        default="infrastructure_governor",
        description="MongoDB database holding the governing configuration documents.",
    )

    MONGO_COLLECTION_ENTERPRISE_CONFIG: str = Field(
        default="enterprise_configuration",
        description="Collection holding the single cascading enterprise-configuration document.",
    )

    MONGO_COLLECTION_NAMING: str = Field(
        default="naming_conventions",
        description="Collection holding the single naming-conventions document.",
    )

    MONGO_COLLECTION_PROJECTS: str = Field(
        default="project_registry",
        description="Collection holding the single project-registry document.",
    )

    POLL_INTERVAL_SECONDS: int = Field(
        default=5,
        description="Interval for the background loop that syncs the live allowlists and invalidates the cached OpenAPI schema.",
    )

    CACHE_TTL_SECONDS: int = Field(
        default=60,
        description="Time-to-live (seconds) for the in-memory cache of resolved config, naming, and project-registry lookups.",
    )

    API_PREFIX: StrictStr = Field(
        default="/api/v1/infra",
        description="Root path under which the Config API is served.",
    )

    API_TAGS: List[str] = Field(
        default=["v1 - Infrastructure Config"],
        description="Tags used for OpenAPI documentation grouping.",
    )


config = ConfigV1Settings()
