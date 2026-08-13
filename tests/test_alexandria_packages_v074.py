from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from elyndra.application import ElyndraApplication
from elyndra.cli import build_parser
from elyndra.paths import ElyndraPaths


def _package(root: Path, *, digest_override: str | None = None) -> Path:
    root.mkdir(parents=True)
    source = root / "html-basics.md"
    source.write_text("# HTML\n\nHTML estructura documentos locales.\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "package_id": "programming.web.html-basics",
        "name": "HTML — Fundamentos",
        "version": "1.0.0",
        "tier": "optional",
        "domain": "programming/web/html",
        "language": "es",
        "license_id": "CC-BY-4.0",
        "publisher": "Elyndra test",
        "tags": ["html", "web"],
        "sources": [
            {
                "path": "html-basics.md",
                "title": "HTML — Fundamentos",
                "sha256": digest_override or digest,
            }
        ],
    }
    (root / "elyndra-package.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    return root


def test_alexandria_package_inspect_and_install_are_local_and_unreviewed(
    isolated_home: ElyndraPaths,
) -> None:
    package = _package(Path.home() / "Descargas" / "html-package")
    app = ElyndraApplication.load(isolated_home)

    inspected = app.alexandria_packages.inspect(package)
    installed = app.alexandria_packages.install(
        package,
        actor=app.identity.system_user,
    )

    assert inspected["network_used"] is False
    assert inspected["execution_performed"] is False
    assert inspected["source_count"] == 1
    assert installed["install_status"] == "installed"
    assert installed["sources_reviewed"] is False
    library = app.alexandria.get_library(int(installed["library_id"]))
    assert library is not None
    assert library["reviewed_units"] == 0
    assert library["domain"] == "programming/web/html"


def test_alexandria_package_can_be_disabled_enabled_and_removed(
    isolated_home: ElyndraPaths,
) -> None:
    package = _package(Path.home() / "Descargas" / "toggle-package")
    app = ElyndraApplication.load(isolated_home)
    installed = app.alexandria_packages.install(package, actor=app.identity.system_user)

    disabled = app.alexandria_packages.set_enabled(installed["package_id"], enabled=False)
    enabled = app.alexandria_packages.set_enabled(installed["package_id"], enabled=True)
    removed = app.alexandria_packages.remove(installed["package_id"])

    assert disabled["enabled"] is False
    assert disabled["library_enabled"] is False
    assert enabled["enabled"] is True
    assert removed["package_id"] == installed["package_id"]
    assert app.alexandria_packages.list_all() == []


def test_alexandria_package_rejects_checksum_mismatch(
    isolated_home: ElyndraPaths,
) -> None:
    package = _package(
        Path.home() / "Descargas" / "bad-package",
        digest_override="0" * 64,
    )
    app = ElyndraApplication.load(isolated_home)

    with pytest.raises(ValueError, match="SHA-256 incorrecto"):
        app.alexandria_packages.inspect(package)


def test_alexandria_package_rejects_path_escape(isolated_home: ElyndraPaths) -> None:
    root = Path.home() / "Descargas" / "escape-package"
    root.mkdir(parents=True)
    outside = root.parent / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    digest = hashlib.sha256(outside.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "package_id": "programming.web.escape",
        "name": "Escape",
        "version": "1",
        "domain": "programming/web",
        "language": "es",
        "license_id": "test",
        "sources": [{"path": "../outside.md", "title": "Outside", "sha256": digest}],
    }
    (root / "elyndra-package.json").write_text(json.dumps(manifest), encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    with pytest.raises(ValueError, match="salir de la carpeta"):
        app.alexandria_packages.inspect(root)


def test_alexandria_package_cli_commands_are_exposed() -> None:
    parser = build_parser()

    inspect_args = parser.parse_args(["alexandria", "package-inspect", "/tmp/package"])
    install_args = parser.parse_args(
        ["alexandria", "package-install", "/tmp/package", "--approve"]
    )
    list_args = parser.parse_args(["alexandria", "package-list"])

    assert inspect_args.alexandria_command == "package-inspect"
    assert install_args.alexandria_command == "package-install"
    assert list_args.alexandria_command == "package-list"


def test_alexandria_package_rejects_symlink_source(
    isolated_home: ElyndraPaths,
) -> None:
    root = Path.home() / "Descargas" / "symlink-package"
    package = _package(root)
    original = root / "html-basics.md"
    target = root / "real-source.md"
    original.rename(target)
    original.symlink_to(target.name)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest_path = package / "elyndra-package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"][0]["sha256"] = digest
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    with pytest.raises(ValueError, match="enlaces simbólicos"):
        app.alexandria_packages.inspect(package)
