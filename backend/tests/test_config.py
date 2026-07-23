"""Settings' whitespace-stripping — a trailing newline pasted into an env
var (confirmed in production for R2_ACCOUNT_ID) must never survive into a
field's value, since it silently breaks anything that builds a URL/header
from it.
"""
from __future__ import annotations

from app.config import Settings


def test_trailing_newline_in_env_value_is_stripped():
    s = Settings(r2_account_id="abc123def456\n")
    assert s.r2_account_id == "abc123def456"


def test_leading_and_trailing_whitespace_is_stripped():
    s = Settings(supabase_service_role_key="  some-key-value  \n")
    assert s.supabase_service_role_key == "some-key-value"


def test_non_string_fields_are_untouched():
    s = Settings(embeddings_dim=42)
    assert s.embeddings_dim == 42


def test_has_r2_true_even_when_account_id_had_a_trailing_newline():
    s = Settings(
        r2_account_id="abc123\n", r2_access_key_id="key\n", r2_secret_access_key="secret\n",
    )
    assert s.has_r2 is True
    assert "\n" not in s.r2_account_id
