from __future__ import annotations

import os
from pathlib import Path

from elyndra.application import ElyndraApplication
from elyndra.cli import build_parser
from elyndra.paths import ElyndraPaths
from elyndra.router import DeterministicRouter
from elyndra.skills.process import ProcessResult


def _executable(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def _prepend_path(monkeypatch, directory: Path) -> None:
    current = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{current}")


def test_php_skills_are_registered_and_require_approval(
    isolated_home: ElyndraPaths,
    monkeypatch,
) -> None:
    tools = Path.home() / "tools"
    _executable(
        tools / "php",
        "#!/bin/sh\necho \"No syntax errors detected in $2\"\nexit 0\n",
    )
    _prepend_path(monkeypatch, tools)
    php_file = Path.home() / "Proyectos" / "example.php"
    php_file.write_text("<?php echo 'ok';\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    names = {skill.name for skill in app.skills.list_all()}
    assert {
        "php.syntax_validate",
        "composer.validate",
        "phpstan.analyse",
        "phpunit.run",
    } <= names

    denied = app.execute_skill("php.syntax_validate", {"path": str(php_file)})
    assert denied.ok is False
    assert denied.data["approval_required"] is True
    assert denied.data["skill_name"] == "php.syntax_validate"

    result = app.execute_skill(
        "php.syntax_validate",
        {"path": str(php_file)},
        approved=True,
    )
    assert result.ok is True
    assert result.data["returncode"] == 0
    assert result.data["shell"] is False
    assert result.data["command"][1] == "-l"
    assert "Sintaxis PHP válida" in result.message


def test_composer_validate_disables_plugins_scripts_and_network(
    isolated_home: ElyndraPaths,
    monkeypatch,
) -> None:
    tools = Path.home() / "tools"
    _executable(
        tools / "composer",
        "#!/bin/sh\necho \"$@\"\necho \"network=$COMPOSER_DISABLE_NETWORK\"\nexit 0\n",
    )
    _prepend_path(monkeypatch, tools)
    project = Path.home() / "Proyectos" / "store"
    project.mkdir()
    (project / "composer.json").write_text('{"name":"test/store"}', encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "composer.validate",
        {"path": str(project), "strict": True},
        approved=True,
    )

    assert result.ok is True
    assert "--no-plugins" in result.data["command"]
    assert "--no-scripts" in result.data["command"]
    assert "--no-check-publish" in result.data["command"]
    assert "--strict" in result.data["command"]
    assert "network=1" in result.data["stdout"]


def test_phpstan_prefers_project_binary_and_parses_issue_count(
    isolated_home: ElyndraPaths,
) -> None:
    project = Path.home() / "Proyectos" / "quality"
    source = project / "src" / "Example.php"
    source.parent.mkdir(parents=True)
    source.write_text("<?php\n", encoding="utf-8")
    (project / "composer.json").write_text("{}", encoding="utf-8")
    binary = _executable(
        project / "vendor" / "bin" / "phpstan",
        "#!/bin/sh\n"
        "echo '{\"totals\":{\"errors\":1,\"file_errors\":2},\"files\":{}}'\n"
        "exit 1\n",
    )
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "phpstan.analyse",
        {"path": str(source), "level": "max"},
        approved=True,
    )

    assert result.ok is False
    assert result.data["issue_count"] == 3
    assert result.data["command"][0] == str(binary.resolve())
    assert "--error-format=prettyJson" in result.data["command"]
    assert "--level" in result.data["command"]


def test_phpunit_prefers_project_binary_and_forwards_safe_filters(
    isolated_home: ElyndraPaths,
) -> None:
    project = Path.home() / "Proyectos" / "tests-project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    (project / "composer.json").write_text("{}", encoding="utf-8")
    (project / "phpunit.xml").write_text("<phpunit/>", encoding="utf-8")
    binary = _executable(
        project / "vendor" / "bin" / "phpunit",
        "#!/bin/sh\necho \"OK (1 test, 1 assertion)\"\nexit 0\n",
    )
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "phpunit.run",
        {
            "path": str(tests),
            "testsuite": "unit",
            "filter": "testCheckout",
        },
        approved=True,
    )

    assert result.ok is True
    assert result.data["command"][0] == str(binary.resolve())
    assert "--do-not-cache-result" in result.data["command"]
    assert result.data["command"][-1] == str(tests)
    assert "Todas las pruebas PHPUnit pasaron" in result.message


def test_router_recognizes_explicit_php_skill_requests() -> None:
    router = DeterministicRouter()

    syntax = router.route("ejecuta php -l en /tmp/example.php")
    composer = router.route("valida composer.json de /tmp/project")
    phpstan = router.route("analiza con PHPStan /tmp/project")
    phpunit = router.route("ejecuta PHPUnit en /tmp/project")

    assert syntax.skill_name == "php.syntax_validate"
    assert composer.skill_name == "composer.validate"
    assert phpstan.skill_name == "phpstan.analyse"
    assert phpunit.skill_name == "phpunit.run"


def test_php_cli_has_short_explicit_commands() -> None:
    parser = build_parser()

    syntax = parser.parse_args(["php", "syntax", "/tmp/a.php", "--approve"])
    phpstan = parser.parse_args(
        ["php", "phpstan", "/tmp/project", "--level", "max", "--approve"]
    )

    assert syntax.command == "php"
    assert syntax.php_command == "syntax"
    assert syntax.approve is True
    assert phpstan.php_command == "phpstan"
    assert phpstan.level == "max"


def test_php_syntax_accepts_readable_file_outside_persistent_roots(
    isolated_home: ElyndraPaths,
    monkeypatch,
) -> None:
    tools = Path.home() / "external-tools"
    _executable(tools / "php", "#!/bin/sh\necho ok\nexit 0\n")
    _prepend_path(monkeypatch, tools)
    source = Path.home() / "Escritorio" / "outside.php"
    source.parent.mkdir()
    source.write_text("<?php\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "php.syntax_validate",
        {"path": str(source)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["resolved_path"] == str(source.resolve())
    assert result.data["authorization_scope"] == "single_file"
    assert result.data["authorization_source"] == "explicit_approval"


def test_php_syntax_normalizes_path_and_rejects_directory(
    isolated_home: ElyndraPaths,
) -> None:
    directory = Path.home() / "Escritorio" / "folder.php"
    directory.mkdir(parents=True)
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "php.syntax_validate",
        {"path": str(directory.parent / ".." / "Escritorio" / "folder.php")},
        approved=True,
    )

    assert result.ok is False
    assert result.data["resolved_path"] == str(directory.resolve())
    assert "archivo regular" in result.message


def test_php_syntax_rejects_fifo(
    isolated_home: ElyndraPaths,
) -> None:
    fifo = Path.home() / "Escritorio" / "pipe.php"
    fifo.parent.mkdir()
    os.mkfifo(fifo)
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "php.syntax_validate",
        {"path": str(fifo)},
        approved=True,
    )

    assert result.ok is False
    assert "recursos especiales" in result.message


def test_php_syntax_rejects_unreadable_file(
    isolated_home: ElyndraPaths,
    monkeypatch,
) -> None:
    source = Path.home() / "Escritorio" / "private.php"
    source.parent.mkdir()
    source.write_text("<?php\n", encoding="utf-8")
    monkeypatch.setattr("elyndra.skills.php_tools._has_read_access", lambda path: False)
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "php.syntax_validate",
        {"path": str(source)},
        approved=True,
    )

    assert result.ok is False
    assert "permiso de lectura" in result.message


def test_external_project_skills_require_one_time_authorization(
    isolated_home: ElyndraPaths,
) -> None:
    project = Path.home() / "Escritorio" / "external-project"
    project.mkdir(parents=True)
    (project / "composer.json").write_text("{}", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    for skill in ("composer.validate", "phpstan.analyse", "phpunit.run"):
        result = app.execute_skill(skill, {"path": str(project)}, approved=True)
        assert result.ok is False
        assert "--allow-root-once" in result.message


def test_external_project_authorization_is_single_execution(
    isolated_home: ElyndraPaths,
    monkeypatch,
) -> None:
    tools = Path.home() / "composer-tools"
    _executable(tools / "composer", "#!/bin/sh\necho valid\nexit 0\n")
    _prepend_path(monkeypatch, tools)
    project = Path.home() / "Escritorio" / "external-composer"
    project.mkdir(parents=True)
    (project / "composer.json").write_text("{}", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    allowed = app.execute_skill(
        "composer.validate",
        {"path": str(project), "allow_root_once": True},
        approved=True,
    )
    denied_again = app.execute_skill(
        "composer.validate",
        {"path": str(project)},
        approved=True,
    )

    assert allowed.ok is True
    assert allowed.data["authorization_scope"] == "project_once"
    assert allowed.data["authorization_expires_after_execution"] is True
    assert denied_again.ok is False
    assert "--allow-root-once" in denied_again.message


def test_external_project_authorization_requires_boolean_true(
    isolated_home: ElyndraPaths,
) -> None:
    project = Path.home() / "Escritorio" / "typed-authorization"
    project.mkdir(parents=True)
    (project / "composer.json").write_text("{}", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "composer.validate",
        {"path": str(project), "allow_root_once": "true"},
        approved=True,
    )

    assert result.ok is False
    assert "--allow-root-once" in result.message

def test_cli_parses_allow_root_once_and_help() -> None:
    parser = build_parser()

    parsed = parser.parse_args(
        ["php", "phpstan", "/tmp/project", "--approve", "--allow-root-once"]
    )
    php_help = parser.parse_args(["php", "help"])
    skill_help = parser.parse_args(["skill", "help"])

    assert parsed.allow_root_once is True
    assert php_help.php_command == "help"
    assert skill_help.skill_command == "help"


def test_php_syntax_rejects_socket_and_device(
    isolated_home: ElyndraPaths,
    monkeypatch,
) -> None:
    import socket

    desktop = Path.home() / "Escritorio"
    desktop.mkdir()
    socket_path = desktop / "service.php"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    with monkeypatch.context() as short_socket_path:
        short_socket_path.chdir(desktop)
        server.bind(socket_path.name)
    app = ElyndraApplication.load(isolated_home)
    try:
        socket_result = app.execute_skill(
            "php.syntax_validate", {"path": str(socket_path)}, approved=True
        )
    finally:
        server.close()

    device_link = desktop / "device.php"
    device_link.symlink_to("/dev/null")
    device_result = app.execute_skill(
        "php.syntax_validate", {"path": str(device_link)}, approved=True
    )

    assert socket_result.ok is False
    assert "recursos especiales" in socket_result.message
    assert device_result.ok is False
    assert "recursos especiales" in device_result.message


def test_skill_audit_records_authorization_scope_and_real_exit_code(
    isolated_home: ElyndraPaths,
    monkeypatch,
) -> None:
    tools = Path.home() / "audit-tools"
    _executable(tools / "php", "#!/bin/sh\necho bad >&2\nexit 23\n")
    _prepend_path(monkeypatch, tools)
    source = Path.home() / "Escritorio" / "audit.php"
    source.parent.mkdir()
    source.write_text("<?php\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "php.syntax_validate", {"path": str(source)}, approved=True
    )
    event = app.audit.list_recent(1)[0]

    assert result.data["returncode"] == 23
    assert '"authorization_scope": "single_file"' in event["details_json"]
    assert '"returncode": 23' in event["details_json"]
    assert "<?php" not in event["details_json"]


def test_missing_project_tools_are_not_installed_automatically(
    isolated_home: ElyndraPaths,
    monkeypatch,
) -> None:
    project = Path.home() / "Proyectos" / "no-tools"
    project.mkdir(parents=True)
    (project / "composer.json").write_text("{}", encoding="utf-8")
    initial_files = {path.relative_to(project) for path in project.rglob("*")}
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    for skill in ("composer.validate", "phpstan.analyse", "phpunit.run"):
        result = app.execute_skill(skill, {"path": str(project)}, approved=True)
        assert result.ok is False
        assert "No se encontró la herramienta requerida" in result.message

    final_files = {path.relative_to(project) for path in project.rglob("*")}
    assert final_files == initial_files
    assert not (project / "vendor").exists()


def test_skill_timeout_and_truncation_are_recorded_in_audit(
    isolated_home: ElyndraPaths,
    monkeypatch,
) -> None:
    tools = Path.home() / "timeout-tools"
    php = _executable(tools / "php", "#!/bin/sh\nexit 0\n")
    _prepend_path(monkeypatch, tools)
    source = Path.home() / "Escritorio" / "timeout.php"
    source.parent.mkdir()
    source.write_text("<?php\n", encoding="utf-8")

    def fake_run(context, command, *, cwd):
        return ProcessResult(
            command=tuple(command),
            cwd=str(cwd),
            returncode=-15,
            stdout="partial",
            stderr="bounded",
            duration_ms=5001,
            timed_out=True,
            stdout_truncated=False,
            stderr_truncated=True,
        )

    monkeypatch.setattr("elyndra.skills.php_tools._run", fake_run)
    app = ElyndraApplication.load(isolated_home)
    result = app.execute_skill(
        "php.syntax_validate", {"path": str(source)}, approved=True
    )
    event = app.audit.list_recent(1)[0]

    assert result.ok is False
    assert result.data["tool_path"] == str(php.resolve())
    assert result.data["timed_out"] is True
    assert result.data["stderr_truncated"] is True
    assert result.data["timeout_seconds"] == 120
    assert '"timed_out": true' in event["details_json"]
    assert '"stderr_truncated": true' in event["details_json"]
    assert '"timeout_seconds": 120' in event["details_json"]
