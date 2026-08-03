"""
A shared password, and the session token it buys.
═══════════════════════════════════════════════════════════════════════════

WHAT THIS IS AND IS NOT
    It is one password, shared, protecting one person's study tool from the
    open internet. That is the whole threat model: someone finds the URL and
    would otherwise spend your Gemini quota and read your history.

    It is NOT multi-user auth. There are no accounts, no roles, no password
    reset. Adding those would be a week of work protecting a database that
    holds one student's practice questions.

WHY A TOKEN RATHER THAN SENDING THE PASSWORD EACH TIME
    The password would then sit in every request the browser makes, in every
    proxy log along the way, and in the devtools Network tab of any machine
    you have ever opened the app on. A short-lived token limits the damage of
    any one of those leaking, and can be invalidated by changing the secret.

WHY NOT A COOKIE
    The frontend is on Netlify and the API is on another host, so a cookie
    would need SameSite=None plus Secure plus credentialed CORS — three
    things to get exactly right, each of which fails in a way that looks like
    something else. A bearer token in a header has none of that.

WHY stdlib hmac AND NOT A JWT LIBRARY
    The token carries one fact: when it expires. A JWT would add a
    dependency, a spec, and an algorithm-confusion footgun to sign a single
    integer. `hmac.compare_digest` against a SHA-256 signature is the whole
    mechanism, and it fits on a screen.

REFUSING TO BOOT
    In production with no password set, `require_configured` raises and the
    app does not start. That is deliberate: the failure it prevents —
    deploying publicly with the door open and not noticing — is silent,
    and a server that will not start is not.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import time

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: How long a token lasts. Long enough not to interrupt a study session or a
#: mock test; short enough that a token copied off a shared machine stops
#: working within the week.
TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60


class AuthError(Exception):
    """The password was wrong, or the token is invalid or expired."""


def _secret() -> bytes:
    """The signing key.

    Derived from the password when no separate secret is configured, so there
    is one thing to set rather than two. The consequence is worth knowing and
    is the useful behaviour: changing the password invalidates every existing
    token, which is exactly what you want after sharing it with someone you
    would now rather not have.
    """
    material = settings.AUTH_SECRET or settings.AUTH_PASSWORD
    return hashlib.sha256(material.encode("utf-8")).digest()


def enabled() -> bool:
    """Auth is on whenever a password is set.

    Presence IS the switch — a separate AUTH_ENABLED flag would allow the
    combination "password set, auth off", which reads as protected and is not.
    """
    return bool(settings.AUTH_PASSWORD)


def require_configured() -> None:
    """Refuse to run publicly with no password. Called at startup."""
    if settings.is_local or enabled():
        return
    raise RuntimeError(
        "ENV=production with no AUTH_PASSWORD set.\n"
        "  A public deployment without a password exposes your API quota and "
        "your saved history to anyone who finds the URL.\n"
        "  Set AUTH_PASSWORD in the environment, or set ENV=local if this is "
        "not a public deployment."
    )


def check_password(candidate: str) -> bool:
    """Constant-time comparison, so timing does not leak the password."""
    if not enabled():
        return True
    return hmac.compare_digest(candidate.encode("utf-8"), settings.AUTH_PASSWORD.encode())


def issue_token(*, ttl: int = TOKEN_TTL_SECONDS) -> tuple[str, int]:
    """Mint a signed token. Returns (token, unix expiry)."""
    expires_at = int(time.time()) + ttl
    # A random nonce so two tokens minted in the same second differ, and one
    # leaking cannot be recognised as identical to another.
    nonce = secrets.token_urlsafe(8)
    payload = f"{expires_at}.{nonce}"
    signature = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    token = f"{payload}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"
    return token, expires_at


def verify_token(token: str) -> None:
    """Raise AuthError unless `token` is well-formed, signed and unexpired."""
    if not enabled():
        return

    try:
        expires_raw, nonce, signature_raw = token.split(".", 2)
        expires_at = int(expires_raw)
    except (ValueError, AttributeError) as exc:
        raise AuthError("malformed token") from exc

    payload = f"{expires_at}.{nonce}"
    expected = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest()

    # A signature that is not valid base64 is a forgery, not a crash. Without
    # this, junk in the header raises binascii.Error out of the middleware and
    # the client gets a 500 where it should get a 401 and a login screen.
    try:
        given = base64.urlsafe_b64decode(signature_raw + "=" * (-len(signature_raw) % 4))
    except (binascii.Error, ValueError) as exc:
        raise AuthError("bad signature") from exc

    # Signature BEFORE expiry: an unsigned token's expiry claim is worthless,
    # and checking it first would report "expired" for a forgery, which is
    # misleading in the logs.
    if not hmac.compare_digest(expected, given):
        raise AuthError("bad signature")

    if time.time() > expires_at:
        raise AuthError("token expired")


def token_from_header(header: str | None) -> str:
    """Pull the token out of an `Authorization: Bearer <token>` header."""
    if not header:
        raise AuthError("no credentials")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise AuthError("expected an Authorization: Bearer header")
    return value.strip()
