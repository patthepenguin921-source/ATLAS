"""Central configuration, loaded from environment (.env)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # A trailing newline pasted into an env var's value (a common accident in
    # Vercel's/most platforms' env var UI) is invisible in the UI but breaks
    # anything that puts it straight into a URL or header — confirmed in
    # production: R2_ACCOUNT_ID had one, so it landed in the R2 upload host
    # (`{account_id}.r2.cloudflarestorage.com`), and every single upload
    # failed with httpx's "Invalid non-printable ASCII character in URL"
    # rather than anything pointing at R2 or the account id. Stripping every
    # string field here means a stray newline/space in *any* env var — not
    # just this one — can't silently break whatever uses it the same way.
    @field_validator("*", mode="before")
    @classmethod
    def _strip_whitespace(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

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
    atlas_llm_provider: str = "groq"          # groq | gemini (both free) | anthropic (paid, higher quality)

    # Groq — free tier, default. Pinned to specific model IDs (Groq has no
    # floating "-latest" alias the way Gemini does below), which is exactly
    # why the previous defaults broke: Groq deprecated both
    # `llama-3.3-70b-versatile` and `llama-3.1-8b-instant` (announced
    # 06/17/26, shut down 08/16/26 for free/developer-tier usage — see
    # https://console.groq.com/docs/deprecations), and every chat turn
    # failed since neither `complete`'s nor `agentic_complete`'s fallback
    # (Groq -> Gemini) fires here: that only triggers on an HTTP 429, and a
    # decommissioned model returns 400, not 429. Now pointed at Groq's own
    # recommended replacements. Re-check console.groq.com/docs/deprecations
    # if chat breaks again -- there's no way to avoid re-pinning by hand
    # when Groq retires whatever's current now.
    groq_api_key: str = ""
    atlas_groq_model: str = "openai/gpt-oss-120b"
    atlas_groq_fast_model: str = "openai/gpt-oss-20b"

    # Google Gemini — also free (Google AI Studio, https://aistudio.google.com/apikey),
    # with materially higher free-tier rate limits than Groq's and generally
    # stronger reasoning than Llama 3.3 70B. Set ATLAS_LLM_PROVIDER=gemini to use
    # it as the primary provider, or just set GEMINI_API_KEY to enable it as the
    # automatic fallback below without switching the primary.
    #
    # Defaults to Google's floating "-latest" alias, not a dated model string
    # (e.g. "gemini-2.5-flash") -- confirmed in production: a pinned dated
    # model 404s outright once Google retires it for new callers (happened to
    # gemini-2.5-flash within months of release, earlier than its announced
    # deprecation date), which broke every chat turn with no way to recover
    # short of a code change. "-latest" always resolves to a live release, so
    # it can't hard-fail this way -- Google just hot-swaps what it points to
    # over time (with >=2 weeks notice for breaking changes), which can shift
    # quality/cost/behavior but never breaks the endpoint outright. See
    # https://ai.google.dev/gemini-api/docs/latest-model. Pin to a specific
    # dated model instead if predictable behavior matters more than never
    # having to touch this again.
    gemini_api_key: str = ""
    atlas_gemini_model: str = "gemini-flash-latest"
    atlas_gemini_fast_model: str = "gemini-flash-latest"

    # Anthropic / Claude — optional paid upgrade path
    anthropic_api_key: str = ""
    atlas_claude_model: str = "claude-opus-4-8"
    atlas_claude_fast_model: str = "claude-haiku-4-5-20251001"

    # If the primary provider above returns a rate-limit (HTTP 429), retry the
    # same call once against this provider instead of failing the chat turn --
    # e.g. default groq primary + gemini fallback means a request only fails
    # if *both* free tiers are exhausted at once. Empty disables fallback.
    # No-ops automatically if this equals the primary provider or has no
    # credentials configured.
    atlas_llm_fallback_provider: str = "gemini"

    # ---- Web search (optional fallback when the student's own data has nothing) ----
    tavily_api_key: str = ""

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

    # ---- Google OAuth (persistent Drive access for Schoology-linked Google
    # Docs, distinct from NEXT_PUBLIC_GOOGLE_CLIENT_ID's one-off Drive Picker
    # grant) ----
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    # The backend's own public URL — Google's OAuth callback redirects here,
    # and the exact value must be registered as an authorized redirect URI
    # on the OAuth client (Google rejects a mismatch outright).
    atlas_api_base_url: str = ""
    # Where to send the browser after the OAuth callback finishes — a
    # redirect can't return JSON, so this is where the consent flow lands
    # the user back in the app (Settings' Integrations tab).
    atlas_frontend_base_url: str = ""

    @property
    def has_google_oauth(self) -> bool:
        return bool(self.google_oauth_client_id and self.google_oauth_client_secret)

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
        return bool(self._llm_credential(self.atlas_llm_provider))

    def _llm_credential(self, provider: str) -> str:
        return {
            "anthropic": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
        }.get(provider, self.groq_api_key)

    @property
    def has_web_search(self) -> bool:
        return bool(self.tavily_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
