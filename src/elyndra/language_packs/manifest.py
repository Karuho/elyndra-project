from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from elyndra.language_packs.constants import MANIFEST_NAME, MAX_MANIFEST_BYTES

_ID = re.compile(r"[a-z0-9][a-z0-9._-]{2,159}")


def load_manifest(root: Path) -> tuple[dict[str, Any], bytes]:
    path = root / MANIFEST_NAME
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Falta {MANIFEST_NAME} como archivo regular.")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("El manifiesto supera 512 KiB.")
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("El manifiesto no es JSON UTF-8 válido.") from exc
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise ValueError("El manifiesto debe ser un objeto schema 1.")
    logical = str(payload.get("pack_id", ""))
    if not _ID.fullmatch(logical):
        raise ValueError("pack_id inválido.")
    for field in (
        "language",
        "locale",
        "version",
        "builder_version",
        "database_sha256",
        "content_sha256",
        "created_at",
    ):
        if not str(payload.get(field, "")).strip():
            raise ValueError(f"Falta el campo obligatorio {field}.")
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("El manifiesto requiere sources.")
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Cada source debe ser un objeto.")
        for field in (
            "source_id",
            "title",
            "source_version",
            "source_url",
            "input_filename",
            "original_sha256",
            "license_id",
                    "license_text_path",
                    "license_sha256",
            "attribution",
            "transformation_notes",
        ):
            if not str(source.get(field, "")).strip():
                raise ValueError(f"La fuente requiere {field}.")
    return payload, raw
