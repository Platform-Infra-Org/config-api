"""HTTP-level tests for the Config API routes via a bare FastAPI app.

The ``LIVE_ALLOWED_*`` sets are empty here (autouse reset), so coordinate
validation is permissive and we control 422/404/200 purely via route logic.
"""


def _config_params():
    return {
        "space": "core-infrastructure", "network": "backbone-net", "region": "us-east",
        "island": "compute-island-a", "environment": "production", "project": "payment-gateway",
    }


class TestProjectsRoute:
    def test_lists_registered_projects(self, client, api_prefix):
        resp = client.get(f"{api_prefix}/projects")
        assert resp.status_code == 200
        assert resp.json()["projects"] == [
            "payment-gateway", "authentication-service",
            "notification-engine", "data-warehouse-pipeline",
        ]

    def test_empty_registry_returns_404(self, empty_client, api_prefix):
        resp = empty_client.get(f"{api_prefix}/projects")
        assert resp.status_code == 404
        assert "empty" in resp.json()["detail"].lower()


class TestCoordinatesRoute:
    def test_lists_all_coordinate_values(self, client, api_prefix):
        resp = client.get(f"{api_prefix}/coordinates")
        assert resp.status_code == 200
        body = resp.json()
        # Sourced from the enterprise config tree (only core-infrastructure is configured).
        assert body["space"] == ["core-infrastructure"]
        assert body["network"] == ["backbone-net"]
        assert body["region"] == ["us-east"]
        assert body["island"] == ["compute-island-a"]
        assert body["environment"] == ["production", "staging"]
        assert body["projects"] == [
            "authentication-service", "data-warehouse-pipeline",
            "notification-engine", "payment-gateway",
        ]

    def test_empty_catalog_returns_200_empty_arrays(self, empty_client, api_prefix):
        # A discovery endpoint: nothing seeded is valid info, not a 404.
        resp = empty_client.get(f"{api_prefix}/coordinates")
        assert resp.status_code == 200
        assert resp.json() == {
            "space": [], "network": [], "region": [],
            "island": [], "environment": [], "projects": [],
        }

    def test_tree_route_returns_nested_dict(self, client, api_prefix):
        resp = client.get(f"{api_prefix}/coordinates/tree")
        assert resp.status_code == 200
        assert resp.json() == {
            "coordinates": {
                "core-infrastructure": {
                    "backbone-net": {
                        "us-east": {
                            "compute-island-a": ["production", "staging"],
                        }
                    }
                }
            },
            "projects": [
                "authentication-service", "data-warehouse-pipeline",
                "notification-engine", "payment-gateway",
            ],
        }

    def test_tree_route_empty_catalog(self, empty_client, api_prefix):
        resp = empty_client.get(f"{api_prefix}/coordinates/tree")
        assert resp.status_code == 200
        assert resp.json() == {"coordinates": {}, "projects": []}


class TestConfigRoute:
    def test_full_coordinates_resolve_200(self, client, api_prefix):
        resp = client.get(f"{api_prefix}/config", params=_config_params())
        assert resp.status_code == 200
        body = resp.json()
        assert body["configurations"]["cluster_size"] == 20
        assert body["configurations"]["global_timeout_ms"] == 3000
        assert body["metadata"]["environment"] == "production"

    def test_missing_coordinate_returns_422(self, client, api_prefix):
        params = _config_params()
        del params["region"]
        resp = client.get(f"{api_prefix}/config", params=params)
        assert resp.status_code == 422

    def test_no_matching_config_returns_404(self, empty_client, api_prefix):
        # Valid (permissive) coords but no config document -> empty -> 404.
        resp = empty_client.get(f"{api_prefix}/config", params=_config_params())
        assert resp.status_code == 404


class TestNamingRoute:
    def test_no_coordinates_returns_full_dictionary(self, client, api_prefix):
        resp = client.get(f"{api_prefix}/naming")
        assert resp.status_code == 200
        parts = resp.json()["naming_parts"]
        assert set(parts.keys()) == {"network", "region", "island", "environment", "space"}

    def test_coordinates_resolve_token_maps(self, client, api_prefix):
        resp = client.get(f"{api_prefix}/naming", params={"network": "backbone-net", "environment": "staging"})
        assert resp.status_code == 200
        parts = resp.json()["naming_parts"]
        assert parts["network"] == {"host": "bb", "cname": "net"}
        assert parts["environment"] == {"host": "stg", "cname": "stage"}

    def test_missing_naming_document_returns_404(self, empty_client, api_prefix):
        resp = empty_client.get(f"{api_prefix}/naming")
        assert resp.status_code == 404


class TestCoordinateValidationAtHttpLayer:
    """A value outside a populated allowlist is rejected by a ``field_validator``
    during ``Depends()`` model construction. Without the error handler installed
    by ``install_coordinate_validation_error_handler`` this surfaces as a 500;
    with it, it becomes a standard 422 — matching native query-param validation.
    """

    def test_config_disallowed_value_returns_422(self, client, api_prefix):
        from tashtiot_apis_library.fastapi_template import config_api as schemas
        schemas.LIVE_ALLOWED_PROJECTS.update({"payment-gateway"})
        params = _config_params()
        params["project"] = "intruder-app"
        resp = client.get(f"{api_prefix}/config", params=params)
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        # Standard FastAPI validation shape: list of errors with ("query", name) loc.
        assert detail[0]["loc"] == ["query", "project"]
        assert "intruder-app" in detail[0]["msg"]

    def test_naming_disallowed_value_returns_422(self, client, api_prefix):
        # The /naming route uses the all-optional InfraMetadata; the same
        # allowlist rejection must also yield 422 there.
        from tashtiot_apis_library.fastapi_template import config_api as schemas
        schemas.LIVE_ALLOWED_NETWORKS.update({"backbone-net"})
        resp = client.get(f"{api_prefix}/naming", params={"network": "ghost-net"})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["query", "network"]

    def test_allowed_value_still_resolves_200(self, client, api_prefix):
        # Regression: populating the allowlist must not break valid requests.
        from tashtiot_apis_library.fastapi_template import config_api as schemas
        schemas.LIVE_ALLOWED_PROJECTS.update({"payment-gateway"})
        resp = client.get(f"{api_prefix}/config", params=_config_params())
        assert resp.status_code == 200
