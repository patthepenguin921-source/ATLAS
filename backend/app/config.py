"""Central configuration, loaded from environment (.env)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # ---- Supabase ----
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    atlas_storage_bucket: str = "atlas-documents"

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
    atlas_cors_origins: str = "http://localhost:3000"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.atlas_cors_origins.split(",") if o.strip()]

    @property
    def has_supabase(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def has_llm(self) -> bool:
        if self.atlas_llm_provider == "anthropic":
            return bool(self.anthropic_api_key)
        return bool(self.groq_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
