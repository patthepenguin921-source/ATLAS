"""Helpers for pulling Google Suite files (Docs/Slides/Sheets/Drive) that
Schoology courses link to, so their contents become searchable knowledge.

A link attachment in Schoology is just a URL. When it points at Google Drive we
extract the file id, then either export a native Google file to a portable
format (Docs/Slides → PDF, Sheets → CSV) or download a binary Drive file
directly — the same conversion `routers/documents.py` uses for the "Import from
Drive" picker. Downloading requires a Google OAuth access token with read
access to the file; when none is available the caller records the link itself
as knowledge instead (and flags it), per the product decision.
"""
from __future__ import annotations

import re
from typing import Optional

import httpx

# https://docs.google.com/document/d/<id>/edit , /presentation/d/<id> ,
# /spreadsheets/d/<id> , and https://drive.google.com/file/d/<id>/view or
# ?id=<id> — capture the id from any of them.
_GOOGLE_ID_PATTERNS = (
    re.compile(r"docs\.google\.com/(document|presentation|spreadsheets)/d/([A-Za-z0-9_-]+)"),
    re.compile(r"drive\.google\.com/file/d/([A-Za-z0-9_-]+)"),
    re.compile(r"drive\.google\.com/open\?id=([A-Za-z0-9_-]+)"),
    re.compile(r"[?&]id=([A-Za-z0-9_-]+)"),
)

# Google "app" mime for each native editor kind, and how we export it.
_NATIVE_EXPORT = {
    "document": ("application/pdf", ".pdf"),
    "presentation": ("application/pdf", ".pdf"),
    "spreadsheets": ("text/csv", ".csv"),
}


class GoogleFileRef:
    def __init__(self, file_id: str, kind: Optional[str]):
        self.file_id = file_id
        # "document" | "presentation" | "spreadsheets" | None (opaque Drive file)
        self.kind = kind


def is_google_url(url: str) -> bool:
    return bool(url) and ("docs.google.com" in url or "drive.google.com" in url)


def parse_google_url(url: str) -> Optional[GoogleFileRef]:
    """Extract a Drive file id (and native-editor kind) from a Google URL."""
    if not url:
        return None
    m = _GOOGLE_ID_PATTERNS[0].search(url)
    if m:
        return GoogleFileRef(m.group(2), m.group(1))
    for pattern in _GOOGLE_ID_PATTERNS[1:]:
        m = pattern.search(url)
        if m:
            return GoogleFileRef(m.group(1), None)
    return None


async def download_google_file(
    ref: GoogleFileRef, access_token: str, *, name: str = "google-file"
) -> tuple[bytes, str, str]:
    """Download a Drive file's bytes using an OAuth access token.

    Native Docs/Slides are exported to PDF and Sheets to CSV; other Drive files
    are downloaded as-is. Returns ``(content, filename, mime_type)``. Raises on
    any non-2xx response so the caller can fall back to storing the link.
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
        if ref.kind in _NATIVE_EXPORT:
            export_mime, ext = _NATIVE_EXPORT[ref.kind]
            r = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{ref.file_id}/export",
                params={"mimeType": export_mime},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            filename = f"{name}{ext}"
            content_type = export_mime
        else:
            # Unknown/binary Drive file — fetch metadata for its real name/type,
            # then download the raw media.
            meta = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{ref.file_id}",
                params={"fields": "name,mimeType"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            filename = name
            content_type = "application/octet-stream"
            if meta.status_code < 300:
                info = meta.json()
                filename = info.get("name") or name
                content_type = info.get("mimeType") or content_type
            r = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{ref.file_id}",
                params={"alt": "media"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if r.status_code >= 300:
            raise RuntimeError(
                f"Google Drive download failed ({r.status_code}) for {ref.file_id}: {r.text[:200]}"
            )
        return r.content, filename, content_type
