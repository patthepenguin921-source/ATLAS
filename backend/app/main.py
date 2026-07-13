"""Atlas API — the intelligence layer of the Academic Operating System."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import settings
from app.core.supabase_client import supabase
from app.routers import api_router
from app.routers import integrations, profile


@asynccontextmanager
async def lifespan(app: FastAPI):
    await supabase.start()
    try:
        yield
    finally:
        await supabase.stop()


app = FastAPI(
    title="Atlas — Academic Operating System",
    description="Persistent academic intelligence: memory + reasoning + agents.",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
async def health():
    return {
        "status": "ok",
        "version": __version__,
        "env": settings.atlas_env,
        "supabase_configured": settings.has_supabase,
        "claude_configured": settings.has_claude,
        "embeddings_provider": settings.embeddings_provider,
    }


@app.get("/", tags=["system"])
async def root():
    return {"name": "Atlas API", "docs": "/docs", "health": "/health"}


# Mount all resource + intelligence routers under /api/v1
app.include_router(profile.router, prefix="/api/v1")
app.include_router(integrations.router, prefix="/api/v1")
app.include_router(api_router, prefix="/api/v1")
