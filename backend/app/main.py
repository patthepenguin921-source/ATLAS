"""Atlas API — the intelligence layer of the Academic Operating System."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.config import settings
from app.core.r2_client import R2Error, r2
from app.core.supabase_client import SupabaseError, supabase
from app.routers import api_router
from app.routers import integrations, profile


@asynccontextmanager
async def lifespan(app: FastAPI):
    await supabase.start()
    await r2.start()
    try:
        yield
    finally:
        await supabase.stop()
        await r2.stop()


app = FastAPI(
    title="Atlas — Academic Operating System",
    description="Persistent academic intelligence: memory + reasoning + agents.",
    version=__version__,
    lifespan=lifespan,
)

# When ATLAS_CORS_ORIGINS is "*" we can't also send credentials (browsers
# reject Access-Control-Allow-Origin: * with credentials), so fall back to a
# regex that echoes any origin. Otherwise use the explicit allow-list. This is
# what lets cross-origin multipart uploads (Documents) succeed against a
# separately-hosted API.
_cors_origins = settings.cors_origins
_allow_all = "*" in _cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[] if _allow_all else _cors_origins,
    allow_origin_regex=".*" if _allow_all else None,
    allow_credentials=not _allow_all,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(SupabaseError)
async def supabase_error_handler(request: Request, exc: SupabaseError):
    # Surface upstream Supabase failures as structured JSON with the real
    # status instead of an unhandled 500 traceback. Upstream 4xx on our data
    # calls means we sent a bad query, so report it as a 502 bad gateway
    # rather than blaming the client; 503 (not configured) passes through.
    status_code = exc.status if exc.status in (429, 503) else 502
    return JSONResponse(
        status_code=status_code,
        content={"detail": {"source": "supabase", "status": exc.status, "error": exc.detail}},
    )


@app.exception_handler(R2Error)
async def r2_error_handler(request: Request, exc: R2Error):
    status_code = exc.status if exc.status in (429, 503) else 502
    return JSONResponse(
        status_code=status_code,
        content={"detail": {"source": "r2", "status": exc.status, "error": exc.detail}},
    )


@app.get("/health", tags=["system"])
async def health():
    return {
        "status": "ok",
        "version": __version__,
        "env": settings.atlas_env,
        "supabase_configured": settings.has_supabase,
        "r2_configured": settings.has_r2,
        "llm_configured": settings.has_llm,
        "llm_provider": settings.atlas_llm_provider,
        "embeddings_provider": settings.embeddings_provider,
    }


@app.get("/", tags=["system"])
async def root():
    return {"name": "Atlas API", "docs": "/docs", "health": "/health"}


# Mount all resource + intelligence routers under /api/v1
app.include_router(profile.router, prefix="/api/v1")
app.include_router(integrations.router, prefix="/api/v1")
app.include_router(api_router, prefix="/api/v1")
