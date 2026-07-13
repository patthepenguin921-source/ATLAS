"""Authentication — verifies Supabase-issued JWT access tokens.

The frontend authenticates with Supabase Auth and sends the resulting access
token as `Authorization: Bearer <jwt>`. We verify it against the project's JWT
secret (HS256) and resolve the user id (`sub`). The backend then acts on that
user's behalf using the service-role key, always scoping to this user id.
"""
from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException, status

from app.config import settings


@dataclass
class CurrentUser:
    id: str
    email: str | None = None
    role: str = "authenticated"


def _decode(token: str) -> dict:
    if not settings.supabase_jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SUPABASE_JWT_SECRET not configured on the server.",
        )
    try:
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
            options={"verify_aud": False},  # Supabase audience varies; verify signature + exp
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired.")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


async def get_current_user(
    authorization: str | None = Header(default=None),
    x_atlas_dev_user: str | None = Header(default=None),
) -> CurrentUser:
    """Resolve the authenticated user.

    Dev convenience: in development, if no JWT secret is configured you may pass
    `X-Atlas-Dev-User: <uuid>` to act as that user. Never enabled in production.
    """
    if (
        settings.atlas_env == "development"
        and not settings.supabase_jwt_secret
        and x_atlas_dev_user
    ):
        return CurrentUser(id=x_atlas_dev_user, email="dev@atlas.local")

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")

    payload = _decode(authorization.split(" ", 1)[1].strip())
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Token missing subject.")
    return CurrentUser(id=sub, email=payload.get("email"), role=payload.get("role", "authenticated"))


CurrentUserDep = Depends(get_current_user)
