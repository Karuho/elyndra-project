from __future__ import annotations

import os
from pathlib import Path

import pytest

from elyndra.application import ElyndraApplication
from elyndra.cli import build_parser
from elyndra.paths import ElyndraPaths
from elyndra.router import DeterministicRouter


def _web_project(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "index.html").write_text(
        "<!doctype html><html><body><main>Hola</main></body></html>",
        encoding="utf-8",
    )
    (root / "app.css").write_text("body { color: #222; }\n", encoding="utf-8")
    (root / "app.js").write_text("const value = 1;\n", encoding="utf-8")
    (root / "app.ts").write_text("const value: number = 1;\n", encoding="utf-8")
    (root / "package.json").write_text(
        '{"name":"demo-web","scripts":{"test":"ignored"},'
        '"dependencies":{"react":"1"},"devDependencies":{"typescript":"1"}}',
        encoding="utf-8",
    )
    (root / "tsconfig.json").write_text('{"compilerOptions":{"strict":true}}', encoding="utf-8")
    return root


def _executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _prepend_path(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    current = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{current}")


def test_php_verify_without_path_requests_deterministic_clarification(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)

    result = app.ask("php verify")

    assert result.ok is True
    assert result.data["engine"] == "deterministic-router"
    assert result.data["generated"] is False
    assert result.data["intended_skill"] == "php.verify_project"
    assert "Indica la ruta" in result.message


def test_web_router_and_cli_expose_web_workflows() -> None:
    router = DeterministicRouter()
    parser = build_parser()

    verify_route = router.route("verifica el proyecto web /tmp/site")
    inspect_route = router.route("inspecciona el proyecto web /tmp/site")
    js_route = router.route("valida sintaxis JavaScript en /tmp/site")
    verify_args = parser.parse_args(
        [
            "webdev",
            "verify",
            "/tmp/site",
            "--no-typescript",
            "--fail-fast",
            "--approve",
        ]
    )

    assert verify_route.skill_name == "web.verify_project"
    assert inspect_route.skill_name == "web.project_inspect"
    assert js_route.skill_name == "javascript.syntax_validate"
    assert verify_args.webdev_command == "verify"
    assert verify_args.typescript is False
    assert verify_args.fail_fast is True


def test_web_project_inspect_external_requires_one_time_authorization(
    isolated_home: ElyndraPaths,
) -> None:
    project = _web_project(Path.home() / "Escritorio" / "web-external")
    app = ElyndraApplication.load(isolated_home)

    denied = app.execute_skill(
        "web.project_inspect",
        {"path": str(project)},
        approved=True,
    )
    allowed = app.execute_skill(
        "web.project_inspect",
        {"path": str(project), "allow_root_once": True},
        approved=True,
    )

    assert denied.ok is False
    assert "--allow-root-once" in denied.message
    assert allowed.ok is True
    assert allowed.data["authorization_scope"] == "project_once"
    assert allowed.data["inventory"]["frameworks"] == ["React"]
    assert allowed.data["inventory"]["scripts"] == ["test"]


def test_html_and_css_internal_validation_detects_structure(
    isolated_home: ElyndraPaths,
) -> None:
    project = _web_project(Path.home() / "Proyectos" / "invalid-web")
    (project / "broken.html").write_text("<main><section></main>", encoding="utf-8")
    (project / "broken.css").write_text("body { color: red;", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    html = app.execute_skill("html.validate", {"path": str(project)}, approved=True)
    css = app.execute_skill("css.validate", {"path": str(project)}, approved=True)

    assert html.ok is False
    assert html.data["failed_files"] == 1
    assert css.ok is False
    assert css.data["failed_files"] == 1
    assert html.data["shell"] is False
    assert css.data["shell"] is False


def test_javascript_syntax_uses_node_check_and_excludes_node_modules(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _web_project(Path.home() / "Proyectos" / "js-web")
    ignored = project / "node_modules" / "ignored.js"
    ignored.parent.mkdir()
    ignored.write_text("broken", encoding="utf-8")
    tools = Path.home() / "tools-js"
    log = Path.home() / "node-check.log"
    _executable(
        tools / "node",
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$2\" >> \"{log}\"\n"
        "exit 0\n",
    )
    _prepend_path(monkeypatch, tools)
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "javascript.syntax_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["scanned_files"] == 1
    checked = log.read_text(encoding="utf-8")
    assert "app.js" in checked
    assert "ignored.js" not in checked
    assert result.data["shell"] is False


def test_typescript_prefers_project_local_tsc(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _web_project(Path.home() / "Proyectos" / "ts-web")
    log = Path.home() / "tsc.log"
    local_tsc = project / "node_modules" / ".bin" / "tsc"
    _executable(
        local_tsc,
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" > \"{log}\"\n"
        "exit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill("typescript.check", {"path": str(project)}, approved=True)

    assert result.ok is True
    assert result.data["tool_source"] == "project_local"
    assert result.data["tool_path"] == str(local_tsc.resolve())
    assert "--noEmit --pretty false --project tsconfig.json" in log.read_text(
        encoding="utf-8"
    )


def test_web_verify_is_partial_without_node_or_typescript(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _web_project(Path.home() / "Proyectos" / "partial-web")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill("web.verify_project", {"path": str(project)}, approved=True)

    assert result.ok is True
    assert result.data["verification_status"] == "partial"
    statuses = {item["name"]: item["status"] for item in result.data["stages"]}
    assert statuses["html"] == "passed"
    assert statuses["css"] == "passed"
    assert statuses["javascript"] == "unavailable"
    assert statuses["typescript"] == "unavailable"
    run = app.verification_runs.get(result.data["verification_run_id"])
    assert run is not None
    assert run["toolchain"] == "web"


def test_web_profile_persists_safe_settings(isolated_home: ElyndraPaths) -> None:
    project = _web_project(Path.home() / "Proyectos" / "profile-web")
    app = ElyndraApplication.load(isolated_home)

    profile = app.web_profiles.save(
        project,
        actor=app.identity.system_user,
        html_enabled=True,
        css_enabled=False,
        javascript_enabled=True,
        typescript_enabled=False,
        fail_fast=True,
        require_tools=True,
        max_files=987,
        exclude_paths=["node_modules", "dist"],
    )

    assert profile["html_enabled"] is True
    assert profile["css_enabled"] is False
    assert profile["typescript_enabled"] is False
    assert profile["fail_fast"] is True
    assert profile["max_files"] == 987
    assert profile["exclude_paths"] == ["node_modules", "dist"]


def test_control_center_reports_web_profiles_verifications_and_packages(
    isolated_home: ElyndraPaths,
) -> None:
    from elyndra.web.server import ElyndraWebService

    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    overview = service.control_overview()

    assert overview["web_profiles"] == 0
    assert overview["web_verifications"] == 0
    assert overview["alexandria_packages"] == 0
    assert service.control_web_verifications() == []
    assert service.control_alexandria_packages() == []
