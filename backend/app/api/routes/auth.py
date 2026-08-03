"""
Signing in.
═══════════════════════════════════════════════════════════════════════════

    POST /auth/login   password  ->  token
    GET  /auth/status  is a password required, and is mine still valid

WHY /auth/status EXISTS
    The frontend needs to know, before showing anything, whether to render a
    login screen at all. On localhost with no password set there should be no
    gate; on the deployed instance there must be. Asking the server removes a
    second copy of that decision from the client.

WHY LOGIN IS RATE LIMITED HARDEST
    It is the one endpoint where guessing pays. A shared password on a public
    URL is exactly the thing a script will hammer, and unlike the model
    endpoints there is no quota to run out and stop it.

    Five attempts a minute makes an online guessing attack useless while
    being far more than a person mistyping needs.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core import auth
from app.core.logging import get_logger
from app.core.ratelimit import Tier, check, client_key

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

#: Attempts per minute, per address. Deliberately far below the other tiers.
LOGIN_ATTEMPTS_PER_MINUTE = 5


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class LoginResponse(BaseModel):
    token: str
    expires_at: int


class StatusResponse(BaseModel):
    #: False on a local instance with no password set — the client then shows
    #: no login screen at all.
    required: bool
    #: Whether the token sent with THIS request is currently valid.
    authenticated: bool


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Exchange the shared password for a session token",
    responses={
        401: {"description": "Wrong password"},
        429: {"description": "Too many attempts"},
    },
)
async def login(request: LoginRequest, http: Request) -> LoginResponse:
    """Check the password and issue a token."""
    identity = client_key(http)

    # Counted before the password is checked, so a wrong guess costs an
    # attempt. Counting only failures would let an attacker probe for free.
    decision = await check(f"login:{identity}", Tier.LLM)
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many attempts. Try again in {decision.retry_after}s.",
            headers={"Retry-After": str(decision.retry_after)},
        )

    if not auth.check_password(request.password):
        # A deliberate pause. It makes rapid online guessing slower still, and
        # costs a real person a fifth of a second once, when they mistype.
        await asyncio.sleep(0.2)
        logger.warning("failed login from %s", identity)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="That password is not right."
        )

    token, expires_at = auth.issue_token()
    logger.info("login from %s", identity)
    return LoginResponse(token=token, expires_at=expires_at)


@router.get("/status", response_model=StatusResponse, summary="Is a password needed")
async def auth_status(http: Request) -> StatusResponse:
    """Answer both questions the client has on load, without a login attempt."""
    if not auth.enabled():
        return StatusResponse(required=False, authenticated=True)

    try:
        auth.verify_token(auth.token_from_header(http.headers.get("authorization")))
        return StatusResponse(required=True, authenticated=True)
    except auth.AuthError:
        return StatusResponse(required=True, authenticated=False)
