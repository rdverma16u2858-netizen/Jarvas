"""
Tests for the shared password.
═══════════════════════════════════════════════════════════════════════════

WHAT MATTERS HERE
    · Closed by default. Every route except the explicit allowlist must 401
      without a token — including routes added after this was written, which
      is what `test_every_route_is_closed_by_default` checks.
    · The app must REFUSE TO BOOT in production with no password. The
      accident it prevents is silent; a server that will not start is not.
    · A forged or expired token must not pass, and the signature must be
      checked before the expiry it claims.
    · Changing the password must invalidate existing tokens.
"""

import time

import pytest
from httpx import AsyncClient

from app.core import auth
from app.core.config import settings

pytestmark = pytest.mark.asyncio

PASSWORD = "a-shared-password"


@pytest.fixture
def locked(monkeypatch):
    """Turn the gate on for one test."""
    monkeypatch.setattr(settings, "AUTH_PASSWORD", PASSWORD)
    monkeypatch.setattr(settings, "AUTH_SECRET", "")
    return PASSWORD


# ── the switch ────────────────────────────────────────────────────────────


async def test_no_password_means_no_gate(monkeypatch) -> None:
    """Localhost with nothing configured must not need a login."""
    monkeypatch.setattr(settings, "AUTH_PASSWORD", "")

    assert auth.enabled() is False
    assert auth.check_password("anything") is True


async def test_setting_a_password_is_what_turns_it_on(locked) -> None:
    """Presence IS the switch. A separate AUTH_ENABLED flag would allow
    "password set, auth off", which reads as protected and is not."""
    assert auth.enabled() is True


async def test_production_without_a_password_refuses_to_boot(monkeypatch) -> None:
    """The failure this prevents — deploying publicly with the door open —
    is silent. A server that will not start is not."""
    monkeypatch.setattr(settings, "AUTH_PASSWORD", "")
    monkeypatch.setattr(settings, "ENV", "production")

    with pytest.raises(RuntimeError, match="AUTH_PASSWORD"):
        auth.require_configured()


async def test_local_without_a_password_boots_fine(monkeypatch) -> None:
    monkeypatch.setattr(settings, "AUTH_PASSWORD", "")
    monkeypatch.setattr(settings, "ENV", "local")

    auth.require_configured()  # must not raise


async def test_production_with_a_password_boots_fine(monkeypatch, locked) -> None:
    monkeypatch.setattr(settings, "ENV", "production")

    auth.require_configured()  # must not raise


# ── tokens ────────────────────────────────────────────────────────────────


async def test_a_minted_token_verifies(locked) -> None:
    token, expires_at = auth.issue_token()

    auth.verify_token(token)  # must not raise
    assert expires_at > time.time()


async def test_a_forged_token_is_rejected(locked) -> None:
    forged = f"{int(time.time()) + 9999}.nonce.not-a-real-signature"

    with pytest.raises(auth.AuthError, match="signature"):
        auth.verify_token(forged)


async def test_an_expired_token_is_rejected(locked) -> None:
    token, _ = auth.issue_token(ttl=-1)

    with pytest.raises(auth.AuthError, match="expired"):
        auth.verify_token(token)


async def test_the_signature_is_checked_before_the_expiry(locked) -> None:
    """An unsigned token's expiry claim is worthless. Reporting "expired" for
    a forgery would also be misleading in the logs."""
    forged_and_expired = "1.nonce.bogus"

    with pytest.raises(auth.AuthError, match="signature"):
        auth.verify_token(forged_and_expired)


async def test_changing_the_password_invalidates_tokens(monkeypatch, locked) -> None:
    """Which is what you want after sharing it with someone you now would
    rather not have."""
    token, _ = auth.issue_token()
    auth.verify_token(token)

    monkeypatch.setattr(settings, "AUTH_PASSWORD", "a-different-password")

    with pytest.raises(auth.AuthError):
        auth.verify_token(token)


async def test_two_tokens_minted_together_differ(locked) -> None:
    first, _ = auth.issue_token()
    second, _ = auth.issue_token()

    assert first != second


async def test_the_header_must_be_a_bearer(locked) -> None:
    assert auth.token_from_header("Bearer abc") == "abc"

    for bad in (None, "", "abc", "Basic abc", "Bearer "):
        with pytest.raises(auth.AuthError):
            auth.token_from_header(bad)


# ── the gate, over HTTP ───────────────────────────────────────────────────


@pytest.fixture
async def guarded_client(locked, client: AsyncClient):
    """The shared client, with the password switched on.

    Reuses `client` rather than building its own so the database tables exist
    — a route that 500s on a missing table would pass a "did it 401?" check
    for entirely the wrong reason. The middleware reads the setting per
    request, so `locked` is all it takes to close the gate.
    """
    return client


async def test_a_protected_route_401s_without_a_token(guarded_client) -> None:
    response = await guarded_client.get("/api/v1/conversations")

    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


async def test_a_token_opens_the_door(guarded_client, locked) -> None:
    token, _ = auth.issue_token()

    response = await guarded_client.get(
        "/api/v1/conversations", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200


async def test_health_stays_open(guarded_client) -> None:
    """Uptime monitors and container health checks are unauthenticated."""
    assert (await guarded_client.get("/api/v1/health")).status_code == 200


async def test_login_stays_open(guarded_client, locked) -> None:
    """You cannot log in through the login gate."""
    response = await guarded_client.post("/api/v1/auth/login", json={"password": locked})

    assert response.status_code == 200
    assert response.json()["token"]


async def test_the_wrong_password_is_refused(guarded_client) -> None:
    response = await guarded_client.post("/api/v1/auth/login", json={"password": "guess"})

    assert response.status_code == 401


async def test_a_preflight_is_not_blocked(guarded_client) -> None:
    """CORS preflight carries no Authorization header by design. Rejecting it
    would surface as an opaque CORS error rather than a 401 the client can
    act on."""
    response = await guarded_client.options(
        "/api/v1/solve",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code < 400


async def test_status_reports_whether_a_password_is_needed(guarded_client, locked) -> None:
    """The client asks this before rendering, so it does not hold a second
    copy of the decision."""
    anonymous = (await guarded_client.get("/api/v1/auth/status")).json()

    token, _ = auth.issue_token()
    signed_in = (
        await guarded_client.get(
            "/api/v1/auth/status", headers={"Authorization": f"Bearer {token}"}
        )
    ).json()

    assert anonymous == {"required": True, "authenticated": False}
    assert signed_in == {"required": True, "authenticated": True}


async def test_every_route_is_closed_by_default(guarded_client) -> None:
    """The allowlist is the security boundary.

    Closed-by-default means a route added later is protected without anyone
    remembering to protect it. This walks the real route table rather than a
    hand-written list, so a new endpoint that should have been guarded fails
    here instead of in production.
    """
    from app.main import app

    allowed_open = {"/", "/api/v1/health", "/api/v1/auth/login", "/api/v1/auth/status"}

    leaked = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}
        if not path.startswith("/api/") or not methods:
            continue
        if path in allowed_open or path.startswith("/api/v1/health"):
            continue
        # Only GETs are probed: a POST would need a valid body to get past
        # validation, and 422 would be indistinguishable from "let through".
        if "GET" not in methods or "{" in path:
            continue

        if (await guarded_client.get(path)).status_code != 401:
            leaked.append(path)

    assert not leaked, f"reachable without a token: {leaked}"
