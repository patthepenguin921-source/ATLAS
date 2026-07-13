"""Authentication — verifies Supabase-issued access tokens.

The frontend authenticates with Supabase Auth and sends the resulting access
token as `Authorization: Bearer <jwt>`. We verify it by asking Supabase's own
Auth API to resolve it (rather than decoding it locally against a shared
secret) — this works regardless of whether the project uses a legacy HS256
JWT secret or the newer asymmetric JWT signing keys, and can never drift out
of sync with the project's actual auth config. The backend then acts on that
user's behalf using the service-role key, always scoping to this user id.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException, status

from app.config import settings
from app.core.supabase_client import SupabaseError, supabase


@dataclass
class CurrentUser:
    id: str
    email: str | None = None
    role: str = "authenticated"


async def get_current_user(
    authorization: str | None = Header(default=None),
    x_atlas_dev_user: str | None = Header(default=None),
) -> CurrentUser:
    """Resolve the authenticated user.

    Dev convenience: in development, if Supabase isn't configured you may pass
    `X-Atlas-Dev-User: <uuid>` to act as that user. Never enabled in production.
    """
    if settings.atlas_env == "development" and not settings.has_supabase and x_atlas_dev_user:
        return CurrentUser(id=x_atlas_dev_user, email="dev@atlas.local")

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")

    token = authorization.split(" ", 1)[1].strip()
    try:
        user = await supabase.get_user(token)
    except SupabaseError as e:
        detail = e.detail if isinstance(e.detail, str) else e.detail.get("msg", str(e.detail))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {detail}")

    sub = user.get("id")
    if not sub:
        raise HTTPException(status_code=401, detail="Token missing subject.")
    return CurrentUser(id=sub, email=user.get("email"), role=user.get("role", "authenticated"))
