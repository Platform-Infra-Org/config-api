"""Validator behaviour for InfraMetadata / RequiredInfraMetadata.

Two guards must hold (per CLAUDE.md): validators are permissive when the
allowlist set is empty, and permissive for omitted (``None``) coordinates.
"""
import pytest
from pydantic import ValidationError

from app.v1.config import schemas
from app.v1.config.schemas import InfraMetadata, RequiredInfraMetadata


# Each coordinate field paired with the allowlist set that governs it.
COORD_TO_ALLOWLIST = [
    ("space", schemas.LIVE_ALLOWED_SPACES),
    ("network", schemas.LIVE_ALLOWED_NETWORKS),
    ("region", schemas.LIVE_ALLOWED_REGIONS),
    ("island", schemas.LIVE_ALLOWED_ISLANDS),
    ("environment", schemas.LIVE_ALLOWED_ENVIRONMENTS),
    ("project", schemas.LIVE_ALLOWED_PROJECTS),
]


class TestEmptyAllowlistIsPermissive:
    @pytest.mark.parametrize("field", [c for c, _ in COORD_TO_ALLOWLIST])
    def test_arbitrary_value_accepted_when_allowlist_empty(self, field):
        # reset_live_allowlists (autouse) guarantees every set is empty here.
        meta = InfraMetadata(**{field: "anything-goes"})
        assert getattr(meta, field) == "anything-goes"


class TestNoneIsPermissive:
    @pytest.mark.parametrize("field,allowlist", COORD_TO_ALLOWLIST)
    def test_none_accepted_even_when_allowlist_populated(self, field, allowlist):
        allowlist.update({"only-valid-value"})
        meta = InfraMetadata(**{field: None})
        assert getattr(meta, field) is None

    def test_all_omitted_is_valid(self):
        meta = InfraMetadata()
        assert meta.model_dump() == {
            "space": None, "network": None, "region": None,
            "island": None, "environment": None, "project": None,
        }


class TestPopulatedAllowlistEnforced:
    @pytest.mark.parametrize("field,allowlist", COORD_TO_ALLOWLIST)
    def test_value_in_allowlist_accepted(self, field, allowlist):
        allowlist.update({"good", "also-good"})
        meta = InfraMetadata(**{field: "good"})
        assert getattr(meta, field) == "good"

    @pytest.mark.parametrize("field,allowlist", COORD_TO_ALLOWLIST)
    def test_value_outside_allowlist_rejected(self, field, allowlist):
        allowlist.update({"good"})
        with pytest.raises(ValidationError) as exc:
            InfraMetadata(**{field: "bad"})
        # The error message echoes the offending value.
        assert "bad" in str(exc.value)


class TestRequiredInfraMetadata:
    def _all_coords(self):
        return dict(
            space="core-infrastructure", network="backbone-net", region="us-east",
            island="compute-island-a", environment="production", project="payment-gateway",
        )

    def test_full_coords_valid(self):
        meta = RequiredInfraMetadata(**self._all_coords())
        assert meta.environment == "production"

    @pytest.mark.parametrize("missing", [
        "space", "network", "region", "island", "environment", "project",
    ])
    def test_missing_any_coordinate_is_error(self, missing):
        coords = self._all_coords()
        del coords[missing]
        with pytest.raises(ValidationError) as exc:
            RequiredInfraMetadata(**coords)
        assert missing in str(exc.value)

    def test_inherits_allowlist_validation(self):
        # Requiredness is overridden but the field_validators are inherited.
        schemas.LIVE_ALLOWED_ENVIRONMENTS.update({"production"})
        coords = self._all_coords()
        coords["environment"] = "not-a-real-env"
        with pytest.raises(ValidationError):
            RequiredInfraMetadata(**coords)
