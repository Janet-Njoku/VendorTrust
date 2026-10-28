"""LOCAL DEVELOPMENT ONLY: a stand-in for Supabase Auth on your own machine.

It emulates the few endpoints VendorTrust uses (password sign-in, refresh, sign-up, TOTP MFA) and
publishes a JWKS, so the real API, the real PostgreSQL database and the unchanged web app can run
together without a Supabase project:

    cd apps/api
    uv run python -m scripts.dev_auth serve                 # http://127.0.0.1:54330
    VT_SUPABASE_URL=http://127.0.0.1:54330 uv run uvicorn app.main:app   # the API trusts its keys
    NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54330 pnpm web:dev         # the web app signs in here

It is deliberately unsafe as an identity provider: it signs tokens for anyone who signs up, with a
throwaway key kept under .local-data. So it refuses to bind anything but the loopback interface,
refuses to run when VT_ENVIRONMENT=production, and the API refuses plain-http auth URLs in
production. Never point a deployed API at it.

Exit status: 0 ok, 2 refused or usage error.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import struct
import sys
import threading
import time
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DIR = REPO_ROOT / ".local-data" / "dev-auth"
DEFAULT_PORT = 54330
DEFAULT_ORIGIN = "http://localhost:3000"
KID = "vt-dev-auth-1"
TOKEN_TTL = 3600
PBKDF2_ROUNDS = 10_000  # this is a fake identity provider: speed beats strength
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class AuthFailure(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# --- TOTP (RFC 6238, SHA-1, 6 digits, 30 s) -------------------------------------------------------


def totp_code(secret: str, at: float, step: int = 30) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = struct.pack(">Q", int(at // step))
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def totp_valid(secret: str, code: str, at: float, window: int = 1) -> bool:
    return any(
        hmac.compare_digest(totp_code(secret, at + drift * 30), code)
        for drift in range(-window, window + 1)
    )


# --- the emulator ---


class DevAuth:
    """Users, factors and token minting. Independent of HTTP so it can be tested directly."""

    def __init__(
        self,
        issuer: str,
        private_key: ec.EllipticCurvePrivateKey,
        state_path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.issuer = issuer
        self._key = private_key
        self._path = state_path
        self._clock = clock
        self._lock = threading.Lock()
        self._users: dict[str, dict[str, Any]] = {}
        self._refresh: dict[str, dict[str, Any]] = {}
        self._challenges: dict[str, dict[str, Any]] = {}
        if state_path and state_path.exists():
            data = json.loads(state_path.read_text())
            self._users = data.get("users", {})
            self._refresh = data.get("refresh", {})

    # persistence
    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"users": self._users, "refresh": self._refresh}, indent=2))
        tmp.chmod(0o600)
        tmp.replace(self._path)

    @staticmethod
    def _hash(password: str, salt: str) -> str:
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS).hex()

    def _user_by_id(self, user_id: str) -> dict[str, Any]:
        for user in self._users.values():
            if user["id"] == user_id:
                return user
        raise AuthFailure(401, "bad_jwt", "Invalid token")

    def _claims(self, access_token: str) -> dict[str, Any]:
        try:
            claims: dict[str, Any] = jwt.decode(
                access_token,
                self._key.public_key(),
                algorithms=["ES256"],
                audience="authenticated",
                issuer=self.issuer,
                options={"require": ["exp", "sub"]},
            )
        except jwt.PyJWTError:
            raise AuthFailure(401, "bad_jwt", "Invalid token") from None
        return claims

    # tokens
    def mint(self, user: dict[str, Any], aal: str = "aal1", ttl: int = TOKEN_TTL) -> str:
        now = int(self._clock())
        amr = [{"method": "password", "timestamp": now}]
        if aal == "aal2":
            amr.insert(0, {"method": "totp", "timestamp": now})
        claims = {
            "iss": self.issuer,
            "aud": "authenticated",
            "sub": user["id"],
            "email": user["email"],
            "role": "authenticated",
            "aal": aal,
            "amr": amr,
            "session_id": str(uuid.uuid4()),
            "is_anonymous": False,
            "user_metadata": {"email_verified": True},
            "iat": now,
            "exp": now + ttl,
        }
        return jwt.encode(claims, self._key, algorithm="ES256", headers={"kid": KID})

    def _public_user(self, user: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": user["id"],
            "email": user["email"],
            "factors": [
                {
                    "id": f["id"],
                    "friendly_name": f["friendly_name"],
                    "factor_type": "totp",
                    "status": "verified" if f["verified"] else "unverified",
                }
                for f in user["factors"]
            ],
        }

    def _session(self, user: dict[str, Any], aal: str) -> dict[str, Any]:
        refresh = secrets.token_urlsafe(32)
        self._refresh[refresh] = {"user_id": user["id"], "aal": aal}
        self._save()
        return {
            "access_token": self.mint(user, aal),
            "token_type": "bearer",
            "expires_in": TOKEN_TTL,
            "expires_at": int(self._clock()) + TOKEN_TTL,
            "refresh_token": refresh,
            "user": self._public_user(user),
        }

    # auth endpoints
    def sign_up(self, email: str, password: str) -> dict[str, Any]:
        email = email.strip().lower()
        if "@" not in email or len(password) < 8:
            raise AuthFailure(422, "weak_password", "Password should be at least 8 characters.")
        with self._lock:
            if email in self._users:
                raise AuthFailure(422, "user_already_exists", "User already registered")
            salt = secrets.token_hex(8)
            user = {
                "id": str(uuid.uuid4()),
                "email": email,
                "salt": salt,
                "password": self._hash(password, salt),
                "factors": [],
            }
            self._users[email] = user
            self._save()
            return self._public_user(user)

    def sign_in(self, email: str, password: str) -> dict[str, Any]:
        user = self._users.get(email.strip().lower())
        expected = user["password"] if user else self._hash("x", "x")
        given = self._hash(password, user["salt"] if user else "x")
        if not user or not hmac.compare_digest(expected, given):
            raise AuthFailure(400, "invalid_credentials", "Invalid login credentials")
        with self._lock:
            return self._session(user, "aal1")

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        with self._lock:
            entry = self._refresh.pop(refresh_token, None)
            if not entry:
                raise AuthFailure(400, "refresh_token_not_found", "Invalid Refresh Token")
            return self._session(self._user_by_id(entry["user_id"]), entry["aal"])

    def user_info(self, access_token: str) -> dict[str, Any]:
        return self._public_user(self._user_by_id(self._claims(access_token)["sub"]))

    # MFA
    def enroll(self, access_token: str, friendly_name: str) -> dict[str, Any]:
        with self._lock:
            user = self._user_by_id(self._claims(access_token)["sub"])
            secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
            factor = {
                "id": str(uuid.uuid4()),
                "friendly_name": friendly_name or "VendorTrust",
                "secret": secret,
                "verified": False,
            }
            user["factors"].append(factor)
            self._save()
            uri = (
                f"otpauth://totp/VendorTrust:{user['email']}?secret={secret}"
                "&issuer=VendorTrust&algorithm=SHA1&digits=6&period=30"
            )
            return {
                "id": factor["id"],
                "type": "totp",
                "friendly_name": factor["friendly_name"],
                "totp": {"qr_code": "", "secret": secret, "uri": uri},
            }

    def _factor(self, user: dict[str, Any], factor_id: str) -> dict[str, Any]:
        for factor in user["factors"]:
            if factor["id"] == factor_id:
                found: dict[str, Any] = factor
                return found
        raise AuthFailure(404, "mfa_factor_not_found", "MFA factor not found")

    def challenge(self, access_token: str, factor_id: str) -> dict[str, Any]:
        user = self._user_by_id(self._claims(access_token)["sub"])
        self._factor(user, factor_id)
        challenge_id = str(uuid.uuid4())
        expires = int(self._clock()) + 300
        self._challenges[challenge_id] = {"factor_id": factor_id, "expires": expires}
        return {"id": challenge_id, "type": "totp", "expires_at": expires}

    def verify(
        self, access_token: str, factor_id: str, challenge_id: str, code: str
    ) -> dict[str, Any]:
        with self._lock:
            user = self._user_by_id(self._claims(access_token)["sub"])
            factor = self._factor(user, factor_id)
            pending = self._challenges.pop(challenge_id, None)
            if (
                not pending
                or pending["factor_id"] != factor_id
                or pending["expires"] < self._clock()
            ):
                raise AuthFailure(422, "mfa_challenge_expired", "MFA challenge has expired")
            if not totp_valid(factor["secret"], code.strip(), self._clock()):
                raise AuthFailure(422, "mfa_verification_failed", "Invalid TOTP code entered")
            factor["verified"] = True
            return self._session(user, "aal2")

    # helpers for the CLI and tests
    def code_for(self, email: str) -> str:
        user = self._users[email.strip().lower()]
        return totp_code(user["factors"][-1]["secret"], self._clock())

    def token_for(self, email: str, aal: str = "aal1") -> str:
        return self.mint(self._users[email.strip().lower()], aal)

    def jwks(self) -> dict[str, Any]:
        jwk: dict[str, Any] = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(self._key.public_key()))
        jwk.update({"kid": KID, "alg": "ES256", "use": "sig"})
        return {"keys": [jwk]}


# --- HTTP ---


def load_or_create_key(directory: Path) -> ec.EllipticCurvePrivateKey:
    path = directory / "key.pem"
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if isinstance(key, ec.EllipticCurvePrivateKey):
            return key
    directory.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o600)
    return key


def make_handler(auth: DevAuth, origin: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "VendorTrustDevAuth"

        def log_message(self, format: str, *args: Any) -> None:
            return  # no request logging: bodies can hold passwords

        def _send(self, status: int, body: dict[str, Any] | None) -> None:
            payload = json.dumps(body if body is not None else {}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(payload)

        def _json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 64 * 1024:
                raise AuthFailure(413, "too_large", "Request too large")
            try:
                parsed = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                raise AuthFailure(400, "bad_json", "Malformed JSON") from None
            return parsed if isinstance(parsed, dict) else {}

        def _bearer(self) -> str:
            header = self.headers.get("Authorization", "")
            if not header.lower().startswith("bearer "):
                raise AuthFailure(401, "no_authorization", "Missing authorization")
            return header[7:].strip()

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers", "apikey, authorization, content-type, x-client-info"
            )
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
            self.end_headers()

        def do_GET(self) -> None:
            self._route("GET")

        def do_POST(self) -> None:
            self._route("POST")

        def _route(self, method: str) -> None:
            parts = urlsplit(self.path)
            path = parts.path.rstrip("/")
            query = parse_qs(parts.query)
            try:
                self._send(200, self._dispatch(method, path, query))
            except AuthFailure as failure:
                self._send(
                    failure.status,
                    {"code": failure.status, "error_code": failure.code, "msg": failure.message},
                )

        def _dispatch(self, method: str, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
            if method == "GET" and path.endswith("/.well-known/jwks.json"):
                return auth.jwks()
            if method == "GET" and path == "/auth/v1/user":
                return auth.user_info(self._bearer())
            if method != "POST":
                raise AuthFailure(404, "not_found", "Not found")
            if path == "/auth/v1/token":
                body = self._json()
                grant = (query.get("grant_type") or [""])[0]
                if grant == "password":
                    return auth.sign_in(str(body.get("email", "")), str(body.get("password", "")))
                if grant == "refresh_token":
                    return auth.refresh(str(body.get("refresh_token", "")))
                raise AuthFailure(400, "unsupported_grant_type", "Unsupported grant type")
            if path == "/auth/v1/signup":
                body = self._json()
                return auth.sign_up(str(body.get("email", "")), str(body.get("password", "")))
            if path == "/auth/v1/logout":
                return {}
            if path == "/auth/v1/factors":
                body = self._json()
                return auth.enroll(self._bearer(), str(body.get("friendly_name", "")))
            segments = path.split("/")
            if len(segments) == 6 and segments[:4] == ["", "auth", "v1", "factors"]:
                factor_id, action = segments[4], segments[5]
                if action == "challenge":
                    return auth.challenge(self._bearer(), factor_id)
                if action == "verify":
                    body = self._json()
                    return auth.verify(
                        self._bearer(),
                        factor_id,
                        str(body.get("challenge_id", "")),
                        str(body.get("code", "")),
                    )
            raise AuthFailure(404, "not_found", "Not found")

    return Handler


def create_server(
    auth: DevAuth, host: str = "127.0.0.1", port: int = DEFAULT_PORT, origin: str = DEFAULT_ORIGIN
) -> ThreadingHTTPServer:
    if host not in LOOPBACK:
        raise ValueError("the dev auth server only binds the loopback interface")
    if os.environ.get("VT_ENVIRONMENT") == "production":
        raise ValueError("the dev auth server must never run with VT_ENVIRONMENT=production")
    return ThreadingHTTPServer((host, port), make_handler(auth, origin))


def build_auth(base_url: str, directory: Path) -> DevAuth:
    return DevAuth(
        issuer=f"{base_url}/auth/v1",
        private_key=load_or_create_key(directory),
        state_path=directory / "state.json",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--dir", type=Path, default=DEFAULT_DIR, help="where the key and users are kept"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the server")
    serve.add_argument("--origin", default=DEFAULT_ORIGIN, help="the web app's origin, for CORS")
    totp = sub.add_parser("totp", help="print the current MFA code of a user's newest factor")
    totp.add_argument("email")
    mint = sub.add_parser("mint", help="print an access token for a user (for curl and tests)")
    mint.add_argument("email")
    mint.add_argument("--aal", choices=["aal1", "aal2"], default="aal1")
    args = parser.parse_args(argv)

    base_url = f"http://127.0.0.1:{args.port}"
    auth = build_auth(base_url, args.dir)
    try:
        if args.command == "serve":
            server = create_server(auth, "127.0.0.1", args.port, args.origin)
            print(f"DEV AUTH (local only, not Supabase) on {base_url}  CORS origin {args.origin}")
            print(f"  API:  VT_SUPABASE_URL={base_url}")
            print(f"  Web:  NEXT_PUBLIC_SUPABASE_URL={base_url}  (any publishable key text)")
            with contextlib.suppress(KeyboardInterrupt):
                server.serve_forever()
            return 0
        if args.command == "totp":
            print(auth.code_for(args.email))
            return 0
        print(auth.token_for(args.email, args.aal))
        return 0
    except (ValueError, KeyError, IndexError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
