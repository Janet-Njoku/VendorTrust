"""The local Supabase Auth stand-in: real HTTP, real JWKS verification by the API's own verifier."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.auth import AuthError, verifier_from_settings
from app.config import Settings
from scripts.dev_auth import (
    DevAuth,
    create_server,
    load_or_create_key,
    main,
    totp_code,
    totp_valid,
)


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, DevAuth]]:
    key = ec.generate_private_key(ec.SECP256R1())
    # The issuer needs the port, which is only known after binding; patch it in afterwards.
    auth = DevAuth(issuer="pending", private_key=key, state_path=tmp_path / "state.json")
    httpd = create_server(auth, "127.0.0.1", 0, "http://localhost:3000")
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    auth.issuer = f"{base}/auth/v1"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield base, auth
    httpd.shutdown()
    httpd.server_close()


def call(
    base: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> tuple[int, dict[str, Any], Any]:
    headers = {"Content-Type": "application/json", "apikey": "any-publishable-key"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{base}{path}", data=data, method=method, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"{}"), response.headers
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}"), error.headers


def register(
    base: str, email: str = "ana@example.invalid", password: str = "correct horse"
) -> dict[str, Any]:
    status, user, _ = call(base, "POST", "/auth/v1/signup", {"email": email, "password": password})
    assert status == 200, user
    status, session, _ = call(
        base, "POST", "/auth/v1/token?grant_type=password", {"email": email, "password": password}
    )
    assert status == 200, session
    return session


def test_sign_up_and_password_sign_in_produce_a_token_the_api_accepts(
    server: tuple[str, DevAuth],
) -> None:
    base, _ = server
    session = register(base)
    assert session["user"]["email"] == "ana@example.invalid"
    # the API's own production verifier fetches the emulator's JWKS over HTTP and checks the token
    verifier = verifier_from_settings(Settings(supabase_url=base))
    identity = verifier.verify(session["access_token"])
    assert str(identity.user_id) == session["user"]["id"]
    claims = jwt.decode(session["access_token"], options={"verify_signature": False})
    assert (claims["aal"], claims["aud"], claims["email"]) == (
        "aal1",
        "authenticated",
        "ana@example.invalid",
    )


def test_a_token_from_another_key_is_rejected_by_the_api(
    server: tuple[str, DevAuth], tmp_path: Path
) -> None:
    base, _ = server
    stranger = DevAuth(f"{base}/auth/v1", ec.generate_private_key(ec.SECP256R1()))
    stranger.sign_up("eve@example.invalid", "password123")
    token = stranger.token_for("eve@example.invalid")
    with pytest.raises(AuthError):
        verifier_from_settings(Settings(supabase_url=base)).verify(token)


def test_wrong_passwords_and_unknown_users_get_the_same_answer(server: tuple[str, DevAuth]) -> None:
    base, _ = server
    register(base)
    for email, password in (
        ("ana@example.invalid", "wrong password"),
        ("nobody@example.invalid", "correct horse"),
    ):
        status, body, _ = call(
            base,
            "POST",
            "/auth/v1/token?grant_type=password",
            {"email": email, "password": password},
        )
        assert (status, body["error_code"]) == (400, "invalid_credentials")


def test_registration_rules(server: tuple[str, DevAuth]) -> None:
    base, _ = server
    register(base)
    assert (
        call(
            base,
            "POST",
            "/auth/v1/signup",
            {"email": "ana@example.invalid", "password": "correct horse"},
        )[0]
        == 422
    )
    assert (
        call(
            base, "POST", "/auth/v1/signup", {"email": "new@example.invalid", "password": "short"}
        )[1]["error_code"]
        == "weak_password"
    )
    assert (
        call(base, "POST", "/auth/v1/signup", {"email": "not-an-email", "password": "long enough"})[
            0
        ]
        == 422
    )


def test_refresh_rotates_the_token_and_cannot_be_replayed(server: tuple[str, DevAuth]) -> None:
    base, _ = server
    session = register(base)
    status, fresh, _ = call(
        base,
        "POST",
        "/auth/v1/token?grant_type=refresh_token",
        {"refresh_token": session["refresh_token"]},
    )
    assert status == 200 and fresh["refresh_token"] != session["refresh_token"]
    assert (
        call(
            base,
            "POST",
            "/auth/v1/token?grant_type=refresh_token",
            {"refresh_token": session["refresh_token"]},
        )[0]
        == 400
    )


def test_mfa_enrollment_challenge_and_verification_upgrade_the_session_to_aal2(
    server: tuple[str, DevAuth],
) -> None:
    base, auth = server
    session = register(base)
    token = session["access_token"]
    status, factor, _ = call(
        base, "POST", "/auth/v1/factors", {"factor_type": "totp", "friendly_name": "phone"}, token
    )
    assert status == 200
    assert factor["totp"]["uri"].startswith(
        "otpauth://totp/VendorTrust:ana@example.invalid?secret="
    )
    _, info, _ = call(base, "GET", "/auth/v1/user", token=token)
    assert [f["status"] for f in info["factors"]] == ["unverified"]

    status, challenge, _ = call(
        base, "POST", f"/auth/v1/factors/{factor['id']}/challenge", token=token
    )
    assert status == 200
    bad = call(
        base,
        "POST",
        f"/auth/v1/factors/{factor['id']}/verify",
        {"challenge_id": challenge["id"], "code": "000000"},
        token,
    )
    assert (bad[0], bad[1]["error_code"]) == (422, "mfa_verification_failed")

    _, challenge, _ = call(base, "POST", f"/auth/v1/factors/{factor['id']}/challenge", token=token)
    code = auth.code_for("ana@example.invalid")
    status, upgraded, _ = call(
        base,
        "POST",
        f"/auth/v1/factors/{factor['id']}/verify",
        {"challenge_id": challenge["id"], "code": code},
        token,
    )
    assert status == 200
    claims = jwt.decode(upgraded["access_token"], options={"verify_signature": False})
    assert claims["aal"] == "aal2"
    assert {m["method"] for m in claims["amr"]} == {"totp", "password"}
    assert verifier_from_settings(Settings(supabase_url=base)).verify(upgraded["access_token"])
    _, info, _ = call(base, "GET", "/auth/v1/user", token=upgraded["access_token"])
    assert [f["status"] for f in info["factors"]] == ["verified"]
    # a challenge can be used once
    assert (
        call(
            base,
            "POST",
            f"/auth/v1/factors/{factor['id']}/verify",
            {"challenge_id": challenge["id"], "code": code},
            token,
        )[0]
        == 422
    )


def test_signing_in_again_is_aal1_until_mfa_is_completed(server: tuple[str, DevAuth]) -> None:
    base, auth = server
    session = register(base)
    _, factor, _ = call(
        base, "POST", "/auth/v1/factors", {"friendly_name": "x"}, session["access_token"]
    )
    _, challenge, _ = call(
        base, "POST", f"/auth/v1/factors/{factor['id']}/challenge", token=session["access_token"]
    )
    call(
        base,
        "POST",
        f"/auth/v1/factors/{factor['id']}/verify",
        {"challenge_id": challenge["id"], "code": auth.code_for("ana@example.invalid")},
        session["access_token"],
    )
    _, again, _ = call(
        base,
        "POST",
        "/auth/v1/token?grant_type=password",
        {"email": "ana@example.invalid", "password": "correct horse"},
    )
    assert jwt.decode(again["access_token"], options={"verify_signature": False})["aal"] == "aal1"
    assert again["user"]["factors"][0]["status"] == "verified"


def test_endpoints_need_a_valid_token(server: tuple[str, DevAuth]) -> None:
    base, _ = server
    assert call(base, "GET", "/auth/v1/user")[0] == 401
    assert call(base, "GET", "/auth/v1/user", token="garbage")[0] == 401
    assert call(base, "POST", "/auth/v1/factors", {"friendly_name": "x"}, "garbage")[0] == 401


def test_cors_allows_only_the_web_origin(server: tuple[str, DevAuth]) -> None:
    base, _ = server
    request = urllib.request.Request(f"{base}/auth/v1/token", method="OPTIONS")  # noqa: S310
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        assert response.status == 204
        assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
        assert "authorization" in response.headers["Access-Control-Allow-Headers"]
    status, _, headers = call(base, "GET", "/.well-known/jwks.json")
    assert status == 200 and headers["Access-Control-Allow-Origin"] == "http://localhost:3000"


def test_unknown_routes_and_methods_are_404(server: tuple[str, DevAuth]) -> None:
    base, _ = server
    assert call(base, "GET", "/nope")[0] == 404
    assert call(base, "POST", "/auth/v1/token?grant_type=magic", {})[0] == 400


def test_users_and_the_key_survive_a_restart(tmp_path: Path) -> None:
    key_dir = tmp_path / "dev-auth"
    first = DevAuth("http://x/auth/v1", load_or_create_key(key_dir), key_dir / "state.json")
    first.sign_up("ana@example.invalid", "correct horse")
    second = DevAuth("http://x/auth/v1", load_or_create_key(key_dir), key_dir / "state.json")
    assert (
        second.sign_in("ana@example.invalid", "correct horse")["user"]["email"]
        == "ana@example.invalid"
    )
    assert (key_dir / "key.pem").stat().st_mode & 0o077 == 0
    assert (key_dir / "state.json").stat().st_mode & 0o077 == 0


def test_totp_matches_the_rfc_6238_test_vector() -> None:
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # ASCII "12345678901234567890" in base32
    assert totp_code(secret, 59) == "287082"
    assert totp_valid(secret, "287082", 59)
    assert totp_valid(secret, "287082", 59 + 30)  # one step of drift is tolerated
    assert not totp_valid(secret, "287082", 59 + 90)
    assert not totp_valid(secret, "000000", 59)


def test_it_refuses_non_loopback_binding_and_production(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = DevAuth("http://x", ec.generate_private_key(ec.SECP256R1()))
    for host in ("0.0.0.0", "192.168.1.5", "example.com"):  # noqa: S104
        with pytest.raises(ValueError, match="loopback"):
            create_server(auth, host, 0)
    monkeypatch.setenv("VT_ENVIRONMENT", "production")
    with pytest.raises(ValueError, match="production"):
        create_server(auth, "127.0.0.1", 0)


def test_the_command_line_prints_tokens_and_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "d"
    auth = DevAuth(
        "http://127.0.0.1:54399/auth/v1", load_or_create_key(directory), directory / "state.json"
    )
    auth.sign_up("ana@example.invalid", "correct horse")
    auth.enroll(auth.token_for("ana@example.invalid"), "x")
    assert (
        main(
            [
                "--port",
                "54399",
                "--dir",
                str(directory),
                "mint",
                "ana@example.invalid",
                "--aal",
                "aal2",
            ]
        )
        == 0
    )
    token = capsys.readouterr().out.strip()
    assert jwt.decode(token, options={"verify_signature": False})["aal"] == "aal2"
    assert main(["--port", "54399", "--dir", str(directory), "totp", "ana@example.invalid"]) == 0
    assert len(capsys.readouterr().out.strip()) == 6
    assert main(["--port", "54399", "--dir", str(directory), "mint", "ghost@example.invalid"]) == 2
