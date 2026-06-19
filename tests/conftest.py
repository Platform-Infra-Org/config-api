"""Shared fixtures.

The seed documents mirror ``seed_config.py`` so provider/route tests exercise the
same shapes the service runs against in production. Keep these in sync if the
governing document shapes change.
"""
import copy

import pytest
from fastapi import FastAPI

from app.v1.config import schemas
from app.v1.config.conf import config
from app.v1.config.errors import install_coordinate_validation_error_handler
from app.v1.config.provider import MongoConfigProvider
from app.v1.config.routes import get_v1_config_router
from tests.fakes import FakeCollection, FakeMongoClient


ENTERPRISE_CONFIG_DOC = {
    "doc_type": "enterprise_configuration",
    "config": {"global_timeout_ms": 3000, "monitoring_provider": "datadog"},
    "space": {
        "core-infrastructure": {
            "config": {"space_policy_class": "tier-1-governed"},
            "network": {
                "backbone-net": {
                    "config": {"ntp_server": "pool.ntp.org", "dns_servers": ["10.0.0.1", "10.0.0.2"]},
                    "region": {
                        "us-east": {
                            "config": {"aws_vpc_id": "vpc-0a1b2c3d"},
                            "island": {
                                "compute-island-a": {
                                    "config": {"cluster_size": 5},
                                    "environment": {
                                        "staging": {"config": {}},
                                        "production": {"config": {"cluster_size": 20, "debug_mode": False}},
                                    },
                                }
                            },
                        }
                    },
                }
            },
        }
    },
}

NAMING_DOC = {
    "doc_type": "naming_conventions",
    "network": {"backbone-net": {"host": "bb", "cname": "net"}},
    "region": {"us-east": {"host": "use1", "cname": "east"}},
    "island": {"compute-island-a": {"host": "isla", "cname": "alpha"}},
    "environment": {
        "staging": {"host": "stg", "cname": "stage"},
        "production": {"host": "prd", "cname": "prod"},
    },
    "space": {
        "core-infrastructure": "core.internal",
        "tenant-alpha": "alpha.tenant.com",
    },
}

PROJECT_REGISTRY_DOC = {
    "doc_type": "project_registry",
    "projects": [
        "payment-gateway",
        "authentication-service",
        "notification-engine",
        "data-warehouse-pipeline",
    ],
}

ALL_SEED_DOCS = [ENTERPRISE_CONFIG_DOC, NAMING_DOC, PROJECT_REGISTRY_DOC]


@pytest.fixture
def seed_docs():
    """Fresh deep copies of the three governing documents."""
    return [copy.deepcopy(d) for d in ALL_SEED_DOCS]


@pytest.fixture(autouse=True)
def reset_live_allowlists():
    """The ``LIVE_ALLOWED_*`` sets are mutable module globals shared across the
    process. Reset them around every test so validator/OpenAPI behaviour is
    deterministic and isolated."""
    sets = [
        schemas.LIVE_ALLOWED_NETWORKS, schemas.LIVE_ALLOWED_REGIONS,
        schemas.LIVE_ALLOWED_ISLANDS, schemas.LIVE_ALLOWED_ENVIRONMENTS,
        schemas.LIVE_ALLOWED_SPACES, schemas.LIVE_ALLOWED_PROJECTS,
    ]
    for s in sets:
        s.clear()
    yield
    for s in sets:
        s.clear()


def make_provider(docs):
    """Build a provider backed by a fake Mongo holding ``docs``."""
    collection = FakeCollection(docs)
    client = FakeMongoClient(collection)
    provider = MongoConfigProvider(client, db_name="testdb", collection_name="global_configs")
    return provider, collection


@pytest.fixture
async def provider(seed_docs):
    """A provider over the full seed set, with a cleared cache.

    ``aiocache``'s in-memory backend shares state across instances, so the cache
    is cleared per test to prevent cross-test bleed.
    """
    prov, collection = make_provider(seed_docs)
    await prov._cache.clear()
    prov._fake_collection = collection  # expose for assertions on Mongo hits
    yield prov
    await prov._cache.clear()


@pytest.fixture
async def empty_provider():
    """A provider whose backing collection has no documents."""
    prov, collection = make_provider([])
    await prov._cache.clear()
    prov._fake_collection = collection
    yield prov
    await prov._cache.clear()


def build_app(provider) -> FastAPI:
    """A bare FastAPI app with just the Config API router wired to ``provider``.

    This intentionally skips ``general_create_app`` and the background poller so
    route tests stay hermetic — they exercise routing + provider, nothing else.
    """
    app = FastAPI()
    app.include_router(get_v1_config_router(provider))
    install_coordinate_validation_error_handler(app)
    return app


@pytest.fixture
def client(provider):
    from fastapi.testclient import TestClient
    return TestClient(build_app(provider))


@pytest.fixture
def empty_client(empty_provider):
    from fastapi.testclient import TestClient
    return TestClient(build_app(empty_provider))


@pytest.fixture
def api_prefix():
    return config.API_PREFIX
