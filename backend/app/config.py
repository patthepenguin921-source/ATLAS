"""Central configuration, loaded from environment (.env)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # ---- Supabase (Postgres + auth) ----
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    # ---- Storage (Cloudflare R2 — S3-compatible) ----
    atlas_storage_bucket: str = "atlas-documents"
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""

    # ---- Reasoning engine (pluggable provider) ----
    atlas_llm_provider: str = "groq"          # groq (free) | anthropic (paid, higher quality)

    # Groq — free tier, default
    groq_api_key: str = ""
    atlas_groq_model: str = "llama-3.3-70b-versatile"
    atlas_groq_fast_model: str = "llama-3.1-8b-instant"

    # Anthropic / Claude — optional upgrade path
    anthropic_api_key: str = ""
    atlas_claude_model: str = "claude-opus-4-8"
    atlas_claude_fast_model: str = "claude-haiku-4-5-20251001"

    # ---- Embeddings ----
    embeddings_provider: str = "local"          # voyage | openai | local
    embeddings_model: str = "voyage-3"
    embeddings_dim: int = 1024
    voyage_api_key: str = ""
    openai_api_key: str = ""

    # ---- Server ----
    atlas_env: str = "development"
    # Encrypts at-rest integration credentials (e.g. PowerSchool portal login)
    # stored in `integrations.secret_ref`. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    atlas_secret_key: str = ""
    # Default to allowing any origin: Atlas authenticates with Bearer tokens
    # (not cookies), so CORS isn't a security boundary here, and a permissive
    # default means cross-origin document uploads work without extra config.
    # Override with a comma-separated allow-list to lock this down.
    atlas_cors_origins: str = "*"
    # Shared secret for automated (unattended) sync triggers — set the same
    # value as Vercel's `CRON_SECRET` project env var (Vercel then sends it
    # automatically as `Authorization: Bearer <value>` on every Cron Job
    # invocation) or hand it to any other scheduler (n8n, etc.) that calls
    # `/integrations/cron/{provider}/sync`. Empty disables the endpoint.
    atlas_cron_secret: str = ""
    # Vercel sets this to "1" in every deployed function automatically — used
    # to detect that we're on serverless infra with no Chromium binary and a
    # hard execution-time limit, where Playwright browser automation
    # (`powerschool_browser.py`) can't run and would otherwise hang until the
    # platform kills the function.
    vercel: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.atlas_cors_origins.split(",") if o.strip()]

    @property
    def is_serverless(self) -> bool:
        return bool(self.vercel)

    @property
    def has_supabase(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def has_r2(self) -> bool:
        return bool(self.r2_account_id and self.r2_access_key_id and self.r2_secret_access_key)

    @property
    def has_llm(self) -> bool:
        if self.atlas_llm_provider == "anthropic":
            return bool(self.anthropic_api_key)
        return bool(self.groq_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
