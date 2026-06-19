"""Inbound JWT auth applied to *this service's* Config API routes.

The auth middleware lives in ``tashtiot-apis-library`` (``AuthMiddleware`` +
``JWTVerifier``); these tests verify it actually protects the Config API's
``/projects``, ``/config`` and ``/naming`` routes when enabled, and that the
auth knobs (mode/algorithms/audience/issuer/header) are env-configurable.

Wiring: ``app/main.py`` now calls ``general_create_app(enable_auth=True, ...)``,
so the capability is compiled in. It is *dual-gated* — the middleware only
registers when the runtime env var ``AUTH_ENABLED=true`` is also set AND exactly
one verification material is configured. With ``AUTH_ENABLED`` false/unset (the
default) auth is a no-op and the service is open. All knobs are read from the
library's settings singleton (i.e. the same ``.env``).

Tokens here use **RS256** (asymmetric): tests sign with a generated RSA private
key and the verifier checks against the corresponding public key in
``LOCAL_PUBKEY`` mode (``AUTH_PUBLIC_KEY_PEM``) — mirroring a realistic
production setup where the service never holds the signing key. Every token
must carry an ``exp`` claim (the verifier requires it).
"""
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from tashtiot_apis_library import general_create_app
from tashtiot_apis_library.fastapi_template._internal.utils import settings as lib_settings
from tashtiot_apis_library.fastapi_template._internal.security import verifier as verifier_mod

from app.v1.config.errors import install_coordinate_validation_error_handler
from app.v1.config.routes import get_v1_config_router


# --------------------------------------------------------------------------- #
# RSA key material (module-scoped: keygen is the slow part)
# --------------------------------------------------------------------------- #

def _rsa_keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv_pem, pub_pem


@pytest.fixture(scope="module")
def rsa_keys():
    return _rsa_keypair()


@pytest.fixture(scope="module")
def other_private_key():
    """An unrelated private key, for forging tokens the public key won't trust."""
    priv_pem, _ = _rsa_keypair()
    return priv_pem


# --------------------------------------------------------------------------- #
# Settings isolation
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def reset_library_auth_settings(monkeypatch):
    """The library ``settings`` is a process-wide singleton and the verifier is
    memoized by ``id(settings)``. Reset both around every test so auth state
    cannot leak between tests (or into the rest of the suite)."""
    verifier_mod._verifier_cache.clear()
    defaults = {
        "AUTH_ENABLED": False,
        "AUTH_HEADER_NAME": "Authorization",
        "AUTH_HS256_SECRET": None,
        "AUTH_JWKS_URL": None,
        "AUTH_PUBLIC_KEY_PEM": None,
        "AUTH_PUBLIC_KEY_PATH": None,
        "AUTH_ALGORITHMS": ["RS256"],
        "AUTH_AUDIENCE": None,
        "AUTH_ISSUER": None,
    }
    for name, value in defaults.items():
        monkeypatch.setattr(lib_settings, name, value)
    yield
    verifier_mod._verifier_cache.clear()


# --------------------------------------------------------------------------- #
# Token + app helpers
# --------------------------------------------------------------------------- #

def _token(private_key, *, algorithm="RS256", kid="test-kid", **claims):
    """Mint a signed bearer token. Defaults to a valid, unexpired RS256 token."""
    payload = {"sub": "config-consumer", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    payload.update(claims)
    return jwt.encode(payload, private_key, algorithm=algorithm, headers={"kid": kid})


def _bearer(token, header_name="Authorization"):
    return {header_name: f"Bearer {token}"}


@pytest.fixture
def auth_client(provider, rsa_keys, monkeypatch):
    """A TestClient over the real factory (auth enabled) + this service's router.

    Mirrors what ``main.py`` builds. Defaults to LOCAL_PUBKEY (RS256) mode with
    the generated public key; ``setting_overrides`` tweak any auth knob, and
    ``enabled`` exercises the runtime gate. The Config router is backed by the
    seeded fake-Mongo ``provider`` so authorized requests resolve real data.
    """
    _, pub_pem = rsa_keys

    def _build(enabled=True, material=True, **setting_overrides):
        monkeypatch.setattr(lib_settings, "AUTH_ENABLED", enabled)
        if material:
            monkeypatch.setattr(lib_settings, "AUTH_PUBLIC_KEY_PEM", pub_pem)
        for name, value in setting_overrides.items():
            monkeypatch.setattr(lib_settings, name, value)
        # Settings must be configured BEFORE construction — add_middlewares builds
        # the verifier (and reads the dual gate) at factory time.
        app = general_create_app(enable_auth=True)
        app.include_router(get_v1_config_router(provider))
        install_coordinate_validation_error_handler(app)
        return TestClient(app)

    return _build


def _full_config_params():
    return {
        "space": "core-infrastructure", "network": "backbone-net", "region": "us-east",
        "island": "compute-island-a", "environment": "production", "project": "payment-gateway",
    }


# --------------------------------------------------------------------------- #
# Rejection of unauthenticated / invalid requests
# --------------------------------------------------------------------------- #

class TestProtectedRoutesRejectUnauthenticated:
    def test_no_token_returns_401(self, auth_client, api_prefix):
        resp = auth_client().get(f"{api_prefix}/projects")
        assert resp.status_code == 401
        assert resp.json() == {"detail": "Not authenticated"}
        assert resp.headers["WWW-Authenticate"] == "Bearer"

    @pytest.mark.parametrize("header", ["token-without-scheme", "Bearer", "Basic abc", "Bearer  "])
    def test_malformed_authorization_header_returns_401(self, auth_client, api_prefix, header):
        resp = auth_client().get(f"{api_prefix}/config", headers={"Authorization": header})
        assert resp.status_code == 401

    def test_garbage_token_returns_401(self, auth_client, api_prefix):
        resp = auth_client().get(f"{api_prefix}/naming", headers=_bearer("not-a-jwt"))
        assert resp.status_code == 401
        assert resp.json() == {"detail": "Invalid token"}

    def test_expired_token_returns_401(self, auth_client, api_prefix, rsa_keys):
        priv_pem, _ = rsa_keys
        expired = _token(priv_pem, exp=datetime.now(timezone.utc) - timedelta(minutes=1))
        resp = auth_client().get(f"{api_prefix}/projects", headers=_bearer(expired))
        assert resp.status_code == 401
        assert resp.json() == {"detail": "Token has expired"}

    def test_token_signed_with_untrusted_key_returns_401(self, auth_client, api_prefix, other_private_key):
        forged = _token(other_private_key)
        resp = auth_client().get(f"{api_prefix}/projects", headers=_bearer(forged))
        assert resp.status_code == 401
        assert resp.json() == {"detail": "Invalid token"}


# --------------------------------------------------------------------------- #
# Valid tokens reach the real routes
# --------------------------------------------------------------------------- #

class TestValidTokenReachesRoutes:
    def test_projects_resolves_with_valid_token(self, auth_client, api_prefix, rsa_keys):
        priv_pem, _ = rsa_keys
        resp = auth_client().get(f"{api_prefix}/projects", headers=_bearer(_token(priv_pem)))
        assert resp.status_code == 200
        assert "payment-gateway" in resp.json()["projects"]

    def test_config_resolves_with_valid_token(self, auth_client, api_prefix, rsa_keys):
        priv_pem, _ = rsa_keys
        resp = auth_client().get(
            f"{api_prefix}/config", params=_full_config_params(), headers=_bearer(_token(priv_pem))
        )
        assert resp.status_code == 200
        assert resp.json()["configurations"]["cluster_size"] == 20

    def test_naming_resolves_with_valid_token(self, auth_client, api_prefix, rsa_keys):
        priv_pem, _ = rsa_keys
        resp = auth_client().get(f"{api_prefix}/naming", headers=_bearer(_token(priv_pem)))
        assert resp.status_code == 200
        assert "network" in resp.json()["naming_parts"]


# --------------------------------------------------------------------------- #
# Configurable knobs (algorithm / audience / issuer / header)
# --------------------------------------------------------------------------- #

class TestConfigurableAuthKnobs:
    def test_algorithm_allowlist_rejects_unlisted_alg(self, auth_client, api_prefix, rsa_keys):
        # AUTH_ALGORITHMS restricts accepted signing algorithms. An RS512 token is
        # rejected when only RS256 is permitted, even though the key is correct.
        priv_pem, _ = rsa_keys
        client = auth_client(AUTH_ALGORITHMS=["RS256"])
        rs512 = _token(priv_pem, algorithm="RS512")
        resp = client.get(f"{api_prefix}/projects", headers=_bearer(rs512))
        assert resp.status_code == 401

    def test_algorithm_allowlist_accepts_configured_alg(self, auth_client, api_prefix, rsa_keys):
        priv_pem, _ = rsa_keys
        client = auth_client(AUTH_ALGORITHMS=["RS256", "RS512"])
        rs512 = _token(priv_pem, algorithm="RS512")
        resp = client.get(f"{api_prefix}/projects", headers=_bearer(rs512))
        assert resp.status_code == 200

    def test_audience_enforced_when_configured(self, auth_client, api_prefix, rsa_keys):
        priv_pem, _ = rsa_keys
        client = auth_client(AUTH_AUDIENCE="infra-config-api")
        good = client.get(f"{api_prefix}/projects", headers=_bearer(_token(priv_pem, aud="infra-config-api")))
        assert good.status_code == 200
        bad = client.get(f"{api_prefix}/projects", headers=_bearer(_token(priv_pem, aud="someone-else")))
        assert bad.status_code == 401
        assert bad.json() == {"detail": "Invalid token audience"}

    def test_issuer_enforced_when_configured(self, auth_client, api_prefix, rsa_keys):
        priv_pem, _ = rsa_keys
        client = auth_client(AUTH_ISSUER="https://idp.example.com/")
        bad = client.get(f"{api_prefix}/projects", headers=_bearer(_token(priv_pem, iss="https://evil.example.com/")))
        assert bad.status_code == 401
        assert bad.json() == {"detail": "Invalid token issuer"}

    def test_custom_auth_header_name_is_honored(self, auth_client, api_prefix, rsa_keys):
        priv_pem, _ = rsa_keys
        client = auth_client(AUTH_HEADER_NAME="X-Auth-Token")
        ok = client.get(f"{api_prefix}/projects", headers=_bearer(_token(priv_pem), header_name="X-Auth-Token"))
        assert ok.status_code == 200
        # The standard Authorization header is now ignored -> 401.
        ignored = client.get(f"{api_prefix}/projects", headers=_bearer(_token(priv_pem)))
        assert ignored.status_code == 401


# --------------------------------------------------------------------------- #
# Gating, exclusions, and fail-fast misconfiguration
# --------------------------------------------------------------------------- #

class TestGatingAndExclusions:
    @pytest.mark.parametrize("path", ["/metrics", "/liveness", "/readiness", "/openapi.json"])
    def test_infrastructure_paths_bypass_auth(self, auth_client, path):
        resp = auth_client().get(path)
        assert resp.status_code != 401

    def test_dual_gate_runtime_switch_off_leaves_routes_open(self, auth_client, api_prefix):
        # enable_auth=True at the code level, but AUTH_ENABLED=False at runtime ->
        # middleware not registered, so the Config routes serve without a token.
        client = auth_client(enabled=False)
        resp = client.get(f"{api_prefix}/projects")
        assert resp.status_code == 200

    def test_enabled_without_material_fails_fast_at_construction(self, auth_client):
        # AUTH_ENABLED=true but no verification material -> AuthConfigError raised
        # at app construction (fail fast), not on the first request.
        from tashtiot_apis_library.fastapi_template._internal.security.errors import AuthConfigError
        with pytest.raises(AuthConfigError):
            auth_client(material=False)


class TestRealAppWiring:
    """The actual app built by ``app.main.create_app`` honors the env gate."""

    def _middleware_names(self, app):
        return [m.cls.__name__ for m in app.user_middleware]

    def test_auth_middleware_registered_when_enabled(self, rsa_keys, monkeypatch):
        from app.main import create_app
        _, pub_pem = rsa_keys
        monkeypatch.setattr(lib_settings, "AUTH_ENABLED", True)
        monkeypatch.setattr(lib_settings, "AUTH_PUBLIC_KEY_PEM", pub_pem)
        app = create_app()
        assert "AuthMiddleware" in self._middleware_names(app)

    def test_auth_middleware_absent_when_disabled(self, monkeypatch):
        # AUTH_ENABLED defaults to False (autouse reset) -> capability compiled in
        # but not registered, so the service stays open. Backward compatible.
        from app.main import create_app
        app = create_app()
        assert "AuthMiddleware" not in self._middleware_names(app)

    def test_openapi_advertises_bearer_scheme_when_enabled(self, rsa_keys, monkeypatch):
        # The library's bearer security scheme (Swagger's Authorize button) must
        # survive this service's enum-injecting openapi wrapper.
        from app.main import create_app
        _, pub_pem = rsa_keys
        monkeypatch.setattr(lib_settings, "AUTH_ENABLED", True)
        monkeypatch.setattr(lib_settings, "AUTH_PUBLIC_KEY_PEM", pub_pem)
        schema = create_app().openapi()
        assert "BearerAuth" in schema.get("components", {}).get("securitySchemes", {})
        assert schema.get("security") == [{"BearerAuth": []}]

    def test_openapi_has_no_security_scheme_when_disabled(self, monkeypatch):
        from app.main import create_app
        schema = create_app().openapi()
        assert "securitySchemes" not in schema.get("components", {})
        assert "security" not in schema
