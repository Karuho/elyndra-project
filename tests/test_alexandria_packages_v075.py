from __future__ import annotations

from pathlib import Path

import pytest

from elyndra.application import ElyndraApplication
from elyndra.cli import build_parser
from elyndra.paths import ElyndraPaths
from elyndra.web.server import ElyndraWebService


def _sources(root: Path) -> list[Path]:
    root.mkdir(parents=True)
    first = root / "guide.md"
    second = root / "guide.txt"
    first.write_text("# Guía\n\nContenido local.\n", encoding="utf-8")
    second.write_text("Contenido complementario.\n", encoding="utf-8")
    return [first, second]


def _create(app: ElyndraApplication, destination: Path, sources: list[Path]) -> dict:
    return app.alexandria_packages.create(
        destination,
        package_id="programming.web.frontend-quality",
        name="Frontend — Calidad",
        version="1.0.0",
        tier="optional",
        domain="programming/web/quality",
        language="es",
        license_id="CC-BY-4.0",
        source_paths=sources,
        publisher="Elyndra test",
        tags=["frontend", "quality"],
    )


def test_package_create_install_and_export_round_trip(
    isolated_home: ElyndraPaths,
) -> None:
    sources = _sources(Path.home() / "Fuentes")
    app = ElyndraApplication.load(isolated_home)
    created = _create(app, Path.home() / "Descargas" / "package-created", sources)
    installed = app.alexandria_packages.install(
        Path(created["package_root"]), actor=app.identity.system_user
    )
    exported = app.alexandria_packages.export(
        installed["package_id"], Path.home() / "Descargas" / "package-exported"
    )

    assert created["creation_status"] == "created"
    assert created["source_count"] == 2
    assert installed["install_status"] == "installed"
    assert exported["exported_package_id"] == installed["package_id"]
    assert app.alexandria_packages.inspect(Path(exported["package_root"]))["source_count"] == 2


def test_package_create_rejects_nonempty_destination(
    isolated_home: ElyndraPaths,
) -> None:
    sources = _sources(Path.home() / "Fuentes-no-vacio")
    destination = Path.home() / "Descargas" / "ocupado"
    destination.mkdir(parents=True)
    (destination / "existing.txt").write_text("x", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    with pytest.raises(ValueError, match="no está vacía"):
        _create(app, destination, sources)


def test_package_create_rejects_symlink_source(
    isolated_home: ElyndraPaths,
) -> None:
    root = Path.home() / "Fuentes-symlink"
    root.mkdir(parents=True)
    real = root / "real.md"
    link = root / "link.md"
    real.write_text("real", encoding="utf-8")
    link.symlink_to(real.name)
    app = ElyndraApplication.load(isolated_home)

    with pytest.raises(ValueError, match="archivo regular"):
        _create(app, Path.home() / "Descargas" / "symlink-create", [link])


def test_package_create_and_export_cli_are_exposed() -> None:
    parser = build_parser()

    create = parser.parse_args(
        [
            "alexandria",
            "package-create",
            "/tmp/package",
            "--package-id",
            "programming.web.demo",
            "--name",
            "Demo",
            "--version",
            "1",
            "--domain",
            "programming/web",
            "--license-id",
            "test",
            "--source",
            "/tmp/source.md",
            "--approve",
        ]
    )
    export = parser.parse_args(
        [
            "alexandria",
            "package-export",
            "programming.web.demo",
            "/tmp/exported",
            "--approve",
        ]
    )

    assert create.alexandria_command == "package-create"
    assert create.source == [Path("/tmp/source.md")]
    assert export.alexandria_command == "package-export"


def test_control_service_creates_installs_and_exports_package(
    isolated_home: ElyndraPaths,
) -> None:
    source = _sources(Path.home() / "Fuentes-web")[0]
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    destination = Path.home() / "Descargas" / "control-created"

    created = service.create_alexandria_package(
        {
            "destination": str(destination),
            "package_id": "personal.cooking.basics",
            "name": "Cocina — Bases",
            "version": "1.0.0",
            "tier": "optional",
            "domain": "personal/cooking",
            "language": "es",
            "license_id": "CC-BY-4.0",
            "sources": [str(source)],
            "tags": ["cocina"],
        }
    )
    installed = service.install_alexandria_package(str(destination))
    exported = service.export_alexandria_package(
        {
            "package_id": installed["package_id"],
            "destination": str(Path.home() / "Descargas" / "control-exported"),
        }
    )

    assert created["network_used"] is False
    assert installed["package_id"] == "personal.cooking.basics"
    assert exported["source_count"] == 1
    actions = [item["action"] for item in app.audit.list_recent(limit=20)]
    assert "web.alexandria.package.create" in actions
    assert "web.alexandria.package.install" in actions
    assert "web.alexandria.package.export" in actions
