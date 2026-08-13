from __future__ import annotations

import os
from pathlib import Path

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths
from elyndra.web.server import ElyndraWebService


def test_web_skill_requires_confirmation_then_executes(
    isolated_home: ElyndraPaths,
    monkeypatch,
) -> None:
    tools = Path.home() / "tools"
    tools.mkdir()
    php = tools / "php"
    execution_log = Path.home() / "php-executions.log"
    php.write_text(
        "#!/bin/sh\n"
        f"echo executed >> {execution_log}\n"
        "echo \"No syntax errors detected in $2\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    php.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ.get('PATH', '')}")
    source = Path.home() / "Proyectos" / "web.php"
    source.write_text("<?php\n", encoding="utf-8")

    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    detail = service.create_chat(title="Skills")
    chat_id = detail["chat"]["id"]
    prompt = f"ejecuta php -l en {source}"

    denied = service.send_message(chat_id, prompt)
    assert denied["ok"] is False
    assert denied["meta"]["approval_required"] is True
    assert denied["meta"]["skill_name"] == "php.syntax_validate"
    assert denied["chat"]["turn_count"] == 0
    assert not execution_log.exists()

    approved = service.send_message(
        chat_id,
        prompt,
        approval_token=denied["meta"]["approval_token"],
    )
    assert approved["ok"] is True
    assert approved["meta"]["engine"] == "local-skill"
    assert approved["meta"]["returncode"] == 0
    assert approved["chat"]["turn_count"] == 1
    assert execution_log.read_text(encoding="utf-8").splitlines() == ["executed"]


def test_web_records_completed_skill_with_tool_findings(
    isolated_home: ElyndraPaths,
    monkeypatch,
) -> None:
    tools = Path.home() / "tools-findings"
    tools.mkdir()
    php = tools / "php"
    php.write_text(
        "#!/bin/sh\necho 'Parse error: unexpected token' >&2\nexit 255\n",
        encoding="utf-8",
    )
    php.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ.get('PATH', '')}")
    source = Path.home() / "Proyectos" / "broken.php"
    source.write_text("<?php broken\n", encoding="utf-8")

    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    detail = service.create_chat(title="Skills")
    chat_id = detail["chat"]["id"]

    prompt = f"ejecuta php -l en {source}"
    pending = service.send_message(chat_id, prompt)
    response = service.send_message(
        chat_id,
        prompt,
        approval_token=pending["meta"]["approval_token"],
    )

    assert response["ok"] is False
    assert response["meta"]["engine"] == "local-skill"
    assert response["meta"]["returncode"] == 255
    assert response["chat"]["turn_count"] == 1
    assert "errores de sintaxis" in response["message"]


def test_web_external_php_file_uses_single_file_authorization(
    isolated_home: ElyndraPaths,
    monkeypatch,
) -> None:
    tools = Path.home() / "web-external-tools"
    tools.mkdir()
    php = tools / "php"
    php.write_text("#!/bin/sh\necho ok\nexit 0\n", encoding="utf-8")
    php.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ.get('PATH', '')}")
    source = Path.home() / "Escritorio" / "web-outside.php"
    source.parent.mkdir()
    source.write_text("<?php\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    chat_id = service.create_chat(title="External")["chat"]["id"]
    prompt = f"ejecuta php -l en {source}"

    pending = service.send_message(chat_id, prompt)
    approved = service.send_message(
        chat_id, prompt, approval_token=pending["meta"]["approval_token"]
    )

    assert pending["meta"]["authorization_scope"] == "single_file"
    assert str(source.resolve()) in pending["meta"]["approval_summary"]
    assert pending["meta"]["action_argv"][-2:] == ["-l", str(source.resolve())]
    assert approved["ok"] is True
    assert approved["chat"]["turn_count"] == 1


def test_web_external_project_grants_project_once_without_persisting(
    isolated_home: ElyndraPaths,
    monkeypatch,
) -> None:
    tools = Path.home() / "web-composer-tools"
    tools.mkdir()
    composer = tools / "composer"
    composer.write_text("#!/bin/sh\necho valid\nexit 0\n", encoding="utf-8")
    composer.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ.get('PATH', '')}")
    project = Path.home() / "Escritorio" / "web-project"
    project.mkdir(parents=True)
    (project / "composer.json").write_text("{}", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    chat_id = service.create_chat(title="External project")["chat"]["id"]
    prompt = f"valida composer.json de {project}"

    pending = service.send_message(chat_id, prompt)
    approved = service.send_message(
        chat_id, prompt, approval_token=pending["meta"]["approval_token"]
    )
    direct_without_once = app.execute_skill(
        "composer.validate", {"path": str(project)}, approved=True
    )

    assert pending["meta"]["authorization_scope"] == "project_once"
    assert pending["meta"]["action_argv"][-1] == str(
        (project / "composer.json").resolve()
    )
    assert approved["ok"] is True
    assert approved["meta"]["authorization_scope"] == "project_once"
    assert direct_without_once.ok is False


def test_web_cancelled_approval_does_not_execute_or_duplicate(
    isolated_home: ElyndraPaths,
) -> None:
    source = Path.home() / "Escritorio" / "cancel.php"
    source.parent.mkdir()
    source.write_text("<?php\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    chat_id = service.create_chat(title="Cancel")["chat"]["id"]

    pending = service.send_message(chat_id, f"ejecuta php -l en {source}")
    detail = service.chat_detail(chat_id)

    assert pending["ok"] is False
    assert pending["meta"]["approval_required"] is True
    assert detail["chat"]["turn_count"] == 0
    assert detail["turns"] == []


def test_web_approval_token_is_single_use(
    isolated_home: ElyndraPaths, monkeypatch
) -> None:
    tools = Path.home() / "single-use-tools"
    tools.mkdir()
    php = tools / "php"
    log = Path.home() / "single-use.log"
    php.write_text(
        f"#!/bin/sh\necho run >> {log}\necho ok\nexit 0\n", encoding="utf-8"
    )
    php.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ.get('PATH', '')}")
    source = Path.home() / "Escritorio" / "once.php"
    source.parent.mkdir()
    source.write_text("<?php\n", encoding="utf-8")
    service = ElyndraWebService(ElyndraApplication.load(isolated_home))
    chat_id = service.create_chat(title="Once")["chat"]["id"]
    prompt = f"ejecuta php -l en {source}"

    pending = service.send_message(chat_id, prompt)
    token = pending["meta"]["approval_token"]
    first = service.send_message(chat_id, prompt, approval_token=token)

    assert first["ok"] is True
    assert log.read_text(encoding="utf-8").splitlines() == ["run"]
    try:
        service.send_message(chat_id, prompt, approval_token=token)
    except ValueError as exc:
        assert "ya fue utilizada" in str(exc)
    else:
        raise AssertionError("La aprobación reutilizada debía rechazarse.")
    assert log.read_text(encoding="utf-8").splitlines() == ["run"]


def test_web_cancelled_token_cannot_be_reused(
    isolated_home: ElyndraPaths, monkeypatch
) -> None:
    tools = Path.home() / "cancel-token-tools"
    tools.mkdir()
    php = tools / "php"
    php.write_text("#!/bin/sh\necho ok\nexit 0\n", encoding="utf-8")
    php.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ.get('PATH', '')}")
    source = Path.home() / "Escritorio" / "cancel-token.php"
    source.parent.mkdir()
    source.write_text("<?php\n", encoding="utf-8")
    service = ElyndraWebService(ElyndraApplication.load(isolated_home))
    chat_id = service.create_chat(title="Cancel token")["chat"]["id"]
    prompt = f"ejecuta php -l en {source}"
    pending = service.send_message(chat_id, prompt)
    token = pending["meta"]["approval_token"]

    assert service.cancel_skill_approval(chat_id, token) is True
    try:
        service.send_message(chat_id, prompt, approval_token=token)
    except ValueError as exc:
        assert "cancelada" in str(exc)
    else:
        raise AssertionError("La aprobación cancelada debía rechazarse.")
    assert service.chat_detail(chat_id)["chat"]["turn_count"] == 0


def test_web_approval_token_expires(
    isolated_home: ElyndraPaths,
) -> None:
    source = Path.home() / "Escritorio" / "expired.php"
    source.parent.mkdir()
    source.write_text("<?php\n", encoding="utf-8")
    service = ElyndraWebService(ElyndraApplication.load(isolated_home))
    chat_id = service.create_chat(title="Expired")["chat"]["id"]
    prompt = f"ejecuta php -l en {source}"
    pending = service.send_message(chat_id, prompt)
    token = pending["meta"]["approval_token"]
    service._approvals._grants[token].expires_monotonic = 0

    try:
        service.send_message(chat_id, prompt, approval_token=token)
    except ValueError as exc:
        assert "expiró" in str(exc)
    else:
        raise AssertionError("La aprobación expirada debía rechazarse.")


def test_web_approval_token_is_bound_to_exact_request(
    isolated_home: ElyndraPaths,
) -> None:
    source = Path.home() / "Escritorio" / "bound.php"
    source.parent.mkdir()
    source.write_text("<?php\n", encoding="utf-8")
    service = ElyndraWebService(ElyndraApplication.load(isolated_home))
    chat_id = service.create_chat(title="Bound")["chat"]["id"]
    prompt = f"ejecuta php -l en {source}"
    pending = service.send_message(chat_id, prompt)

    try:
        service.send_message(
            chat_id,
            prompt + " ahora",
            approval_token=pending["meta"]["approval_token"],
        )
    except ValueError as exc:
        assert "no corresponde" in str(exc)
    else:
        raise AssertionError("La aprobación no debía autorizar otra solicitud.")
