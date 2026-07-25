"""A real upload of a large, densely-laid-out PDF (a College Board Course
and Exam Description) kept sitting "processing" forever across every
approach tried for the pipeline (inline, BackgroundTask, cron) — root cause
turned out to be ingestion.extract_text's layout-aware PDF pass
(pymupdf4llm), a plain CPU-bound call with no timeout of its own, that can
simply hang or run for a very long time on that kind of document.
app.routers.documents._extract_text_bounded exists to bound that call and
fall back to the much-faster (if less layout-accurate) extract_text_fast
instead of never returning at all.
"""
from __future__ import annotations

import asyncio
import time

import app.routers.documents as documents_module


def test_extract_text_bounded_returns_the_fast_path_result_directly(monkeypatch):
    monkeypatch.setattr(documents_module.ingestion, "extract_text", lambda c, m, f: "quick result")

    result = asyncio.run(documents_module._extract_text_bounded(b"x", "application/pdf", "f.pdf"))

    assert result == "quick result"


def test_extract_text_bounded_falls_back_when_the_slow_path_times_out(monkeypatch):
    monkeypatch.setattr(documents_module, "_TEXT_EXTRACTION_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(documents_module, "_TEXT_EXTRACTION_FAST_TIMEOUT_SECONDS", 5)

    def hangs(content, mime_type, filename):
        time.sleep(0.3)  # stands in for pymupdf4llm taking too long
        return "should never be seen"

    monkeypatch.setattr(documents_module.ingestion, "extract_text", hangs)
    monkeypatch.setattr(documents_module.ingestion, "extract_text_fast", lambda c, m, f: "fast fallback result")

    result = asyncio.run(documents_module._extract_text_bounded(b"x", "application/pdf", "f.pdf"))

    assert result == "fast fallback result"


def test_extract_text_bounded_gives_up_gracefully_when_both_paths_time_out(monkeypatch):
    monkeypatch.setattr(documents_module, "_TEXT_EXTRACTION_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(documents_module, "_TEXT_EXTRACTION_FAST_TIMEOUT_SECONDS", 0.05)

    def hangs(content, mime_type, filename):
        time.sleep(0.3)
        return "should never be seen"

    monkeypatch.setattr(documents_module.ingestion, "extract_text", hangs)
    monkeypatch.setattr(documents_module.ingestion, "extract_text_fast", hangs)

    # Never hangs the test itself — the point of the timeout — and degrades
    # to empty text rather than raising, so the caller can still record the
    # document as unindexed instead of crashing.
    result = asyncio.run(documents_module._extract_text_bounded(b"x", "application/pdf", "f.pdf"))

    assert result == ""
