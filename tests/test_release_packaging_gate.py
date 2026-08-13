from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLYFORM_ID = "PolyForm-Noncommercial-1.0.0"
POLYFORM_LICENSE_SHA256 = (
    "ffcca38841adb694b6f380647e15f17c446a4d1656fed51a1e2041d064c94cc8"
)


def test_packaging_declares_pep639_license_and_supported_backend() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["build-system"] == {
        "requires": ["setuptools>=77"],
        "build-backend": "setuptools.build_meta",
    }
    assert project["project"]["license"] == POLYFORM_ID
    assert project["project"]["license-files"] == ["LICENSE", "NOTICE.md"]
    assert not any(
        classifier.startswith("License ::")
        for classifier in project["project"]["classifiers"]
    )


def test_polyform_license_and_required_notices_are_publication_ready() -> None:
    license_bytes = (ROOT / "LICENSE").read_bytes()
    notice_lines = (ROOT / "NOTICE.md").read_text(encoding="utf-8").splitlines()
    required_notices = [line for line in notice_lines if "Required Notice:" in line]

    assert hashlib.sha256(license_bytes).hexdigest() == POLYFORM_LICENSE_SHA256
    assert len(required_notices) == 5
    assert all(line.startswith("Required Notice:") for line in required_notices)
    assert all(not line.startswith(("- ", "> ")) for line in required_notices)


def test_active_packaging_documents_have_no_agpl_fallback() -> None:
    active_files = (
        "LICENSE",
        "NOTICE.md",
        "COMMERCIAL-LICENSING.md",
        "README.md",
        "pyproject.toml",
    )
    active_text = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8") for relative in active_files
    ).casefold()

    assert "agpl" not in active_text
    assert "gnu affero" not in active_text
    assert "elyndra community license" not in active_text
