"""Seed the Config API's MongoDB with the three governing documents.

Destructive: clears the target collection first. Mirrors the seed data used by
the v1 Config API (app/v1/config). Run with the API's MongoDB reachable:

    python seed_config.py
"""
import os
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "infrastructure_governor")
MONGO_COLLECTION = os.environ.get("MONGO_COLLECTION", "global_configs")


def seed_database():
    client = MongoClient(MONGO_URI)
    db = client[MONGO_DB_NAME]
    collection = db[MONGO_COLLECTION]

    # Clear collection out completely to prevent stale artifact noise
    collection.delete_many({})

    # Document A: Enterprise Cascading Hierarchical Configuration Tree
    config_tree = {
        "doc_type": "enterprise_configuration",
        "config": {
            "global_timeout_ms": 3000,
            "monitoring_provider": "datadog"
        },
        "space": {
            "core-infrastructure": {
                "config": {
                    "space_policy_class": "tier-1-governed"
                },
                "network": {
                    "backbone-net": {
                        "config": {
                            "ntp_server": "pool.ntp.org",
                            "dns_servers": ["10.0.0.1", "10.0.0.2"]
                        },
                        "region": {
                            "us-east": {
                                "config": {
                                    "aws_vpc_id": "vpc-0a1b2c3d"
                                },
                                "island": {
                                    "compute-island-a": {
                                        "config": {
                                            "cluster_size": 5
                                        },
                                        "environment": {
                                            "staging": {
                                                "config": {}
                                            },
                                            "production": {
                                                "config": {
                                                    "cluster_size": 20,
                                                    "debug_mode": False
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    # Document B: Network Topology Translation Naming Conventions
    naming_conventions = {
        "doc_type": "naming_conventions",
        "network": {
            "backbone-net": {"host": "bb", "cname": "net"}
        },
        "region": {
            "us-east": {"host": "use1", "cname": "east"}
        },
        "island": {
            "compute-island-a": {"host": "isla", "cname": "alpha"}
        },
        "environment": {
            "staging": {"host": "stg", "cname": "stage"},
            "production": {"host": "prd", "cname": "prod"}
        },
        "space": {
            "core-infrastructure": "core.internal",
            "tenant-alpha": "alpha.tenant.com"
        }
    }

    # Document C: Standalone Global Projects Inventory Registry
    project_registry = {
        "doc_type": "project_registry",
        "projects": [
            "payment-gateway",
            "authentication-service",
            "notification-engine",
            "data-warehouse-pipeline"
        ]
    }

    collection.insert_one(config_tree)
    collection.insert_one(naming_conventions)
    collection.insert_one(project_registry)

    print("Successfully seeded singular hierarchy models into MongoDB.")
    client.close()


if __name__ == "__main__":
    seed_database()
