"""Generate an RSA keypair and a signed RS256 JWT for exercising inbound auth.

The service verifies bearer tokens via the library's JWTVerifier. In offline
local-pubkey mode it checks the token's signature against a public key you
provide (``AUTH_PUBLIC_KEY_PEM`` / ``AUTH_PUBLIC_KEY_PATH``), while the token is
signed with the matching private key. This script produces all three: the
private key (sign side), the public key (verify side), and a ready-to-use token.

Usage:

    python gen_auth_material.py                       # write keys + print a token
    python gen_auth_material.py --sub svc --aud infra-config-api --iss https://idp/
    python gen_auth_material.py --expires-minutes 60 --algorithm RS256
    python gen_auth_material.py --no-write            # print everything, write nothing
    python gen_auth_material.py --private-key jwt_private.pem   # reuse existing keys,
    python gen_auth_material.py --private-key jwt_private.pem --public-key jwt_public.pem
                                                     # mint a fresh token, keep the keys

Then enable auth (see .env.example):

    AUTH_ENABLED=true
    AUTH_PUBLIC_KEY_PATH=./jwt_public.pem      # or paste AUTH_PUBLIC_KEY_PEM
    # AUTH_AUDIENCE / AUTH_ISSUER must match if you pass --aud / --iss

and call a protected route:

    curl -H "Authorization: Bearer <token>" http://localhost:5000/api/v1/infra/projects
"""
from __future__ import annotations  # allow `str | None` hints on Python 3.9

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _public_pem_from_private(private_pem: str) -> str:
    """Derive the SubjectPublicKeyInfo PEM for a private key PEM."""
    private_key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    return private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def generate_keypair(key_size: int = 2048):
    """Return (private_pem, public_pem) as PEM strings."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return private_pem, _public_pem_from_private(private_pem)


def load_keypair(private_path: str, public_path: str | None = None):
    """Load an existing private key (required, used to sign) and its public key.

    The public key is read from ``public_path`` when given, otherwise derived
    from the private key — so passing just the private key is enough.
    """
    private_pem = Path(private_path).read_text()
    if public_path is not None:
        public_pem = Path(public_path).read_text()
    else:
        public_pem = _public_pem_from_private(private_pem)
    return private_pem, public_pem


def mint_token(
    private_pem: str,
    *,
    subject: str,
    algorithm: str = "RS256",
    kid: str = "local-dev-key",
    expires_minutes: int = 30,
    audience: str | None = None,
    issuer: str | None = None,
) -> str:
    """Sign and return an RS256 JWT. Always includes ``exp`` (the verifier
    requires it); adds ``aud``/``iss`` only when supplied so they match the
    service's AUTH_AUDIENCE / AUTH_ISSUER when configured."""
    now = datetime.now(timezone.utc)
    claims = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    if audience is not None:
        claims["aud"] = audience
    if issuer is not None:
        claims["iss"] = issuer
    return jwt.encode(claims, private_pem, algorithm=algorithm, headers={"kid": kid})


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate RSA keys and a signed JWT for inbound auth.")
    parser.add_argument("--sub", default="local-dev", help="Token subject ('sub' claim).")
    parser.add_argument("--aud", default=None, help="Audience ('aud'); set to match AUTH_AUDIENCE.")
    parser.add_argument("--iss", default=None, help="Issuer ('iss'); set to match AUTH_ISSUER.")
    parser.add_argument("--algorithm", default="RS256", help="Signing algorithm (default: RS256).")
    parser.add_argument("--kid", default="local-dev-key", help="Key id placed in the JWT header.")
    parser.add_argument("--expires-minutes", type=int, default=30, help="Token lifetime in minutes.")
    parser.add_argument("--key-size", type=int, default=2048, help="RSA key size in bits.")
    parser.add_argument("--out-dir", default=".", help="Directory for the .pem files.")
    parser.add_argument("--private-name", default="jwt_private.pem", help="Private key filename.")
    parser.add_argument("--public-name", default="jwt_public.pem", help="Public key filename.")
    parser.add_argument("--no-write", action="store_true", help="Print only; do not write key files.")
    parser.add_argument(
        "--private-key",
        default=None,
        help="Path to an existing private key PEM to sign with (skips key generation).",
    )
    parser.add_argument(
        "--public-key",
        default=None,
        help="Path to an existing public key PEM (optional; derived from --private-key if omitted).",
    )
    args = parser.parse_args()

    if args.public_key is not None and args.private_key is None:
        parser.error("--public-key requires --private-key (the private key is needed to sign the JWT).")

    # Reuse existing keys when given, else generate a fresh pair. Existing keys
    # are never written back (they already live on disk); only generated keys are.
    using_existing = args.private_key is not None
    if using_existing:
        private_pem, public_pem = load_keypair(args.private_key, args.public_key)
    else:
        private_pem, public_pem = generate_keypair(args.key_size)
    token = mint_token(
        private_pem,
        subject=args.sub,
        algorithm=args.algorithm,
        kid=args.kid,
        expires_minutes=args.expires_minutes,
        audience=args.aud,
        issuer=args.iss,
    )

    # A filesystem path to advertise as AUTH_PUBLIC_KEY_PATH, when one exists.
    public_key_ref = args.public_key

    if using_existing:
        print(f"Using private key -> {args.private_key}  (signing)")
        if args.public_key is not None:
            print(f"Using public key  -> {args.public_key}  (verify)")
        else:
            print("Public key derived from the private key (no --public-key given).")
    elif not args.no_write:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        priv_path = out_dir / args.private_name
        pub_path = out_dir / args.public_name
        # Restrict the private key to the owner; it must never be committed.
        priv_path.write_text(private_pem)
        priv_path.chmod(0o600)
        pub_path.write_text(public_pem)
        public_key_ref = str(pub_path)
        print(f"Wrote private key -> {priv_path}  (keep secret; chmod 600)")
        print(f"Wrote public key  -> {pub_path}")

    # Show the public key inline whenever there is no file to point at, so the
    # user can paste it into AUTH_PUBLIC_KEY_PEM.
    if public_key_ref is None:
        print()
        print("===== PUBLIC KEY (verify side — paste into AUTH_PUBLIC_KEY_PEM) =====")
        print(public_pem)

    # In print-only generate mode, also surface the private key (it was not written).
    if not using_existing and args.no_write:
        print("===== PRIVATE KEY (sign side — keep secret) =====")
        print(private_pem)

    print()
    print("Configure the service (.env):")
    print("  AUTH_ENABLED=true")
    if public_key_ref is not None:
        print(f"  AUTH_PUBLIC_KEY_PATH={public_key_ref}")
    else:
        print("  AUTH_PUBLIC_KEY_PEM=<the PUBLIC KEY above>")
    if args.algorithm != "RS256":
        print(f'  AUTH_ALGORITHMS=["{args.algorithm}"]')
    if args.aud is not None:
        print(f"  AUTH_AUDIENCE={args.aud}")
    if args.iss is not None:
        print(f"  AUTH_ISSUER={args.iss}")
    print()

    print("===== BEARER TOKEN =====")
    print(token)
    print()
    print("Try it:")
    print(f'  curl -H "Authorization: Bearer {token}" \\')
    print("       http://localhost:5000/api/v1/infra/projects")


if __name__ == "__main__":
    main()
