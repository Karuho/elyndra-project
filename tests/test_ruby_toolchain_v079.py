from __future__ import annotations

from pathlib import Path

import pytest

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths
from elyndra.router import DeterministicRouter
from elyndra.web.server import ElyndraWebService


def _executable(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def _ruby_project(root: Path, *, tests: bool = True) -> Path:
    root.mkdir(parents=True)
    (root / "Gemfile").write_text(
        "source 'https://rubygems.org'\ngem 'rake'\ngem 'rspec'\n",
        encoding="utf-8",
    )
    (root / "Gemfile.lock").write_text("GEM\n", encoding="utf-8")
    source = root / "lib" / "example.rb"
    source.parent.mkdir(parents=True)
    source.write_text(
        "module Example\n  def self.value = 1\nend\n",
        encoding="utf-8",
    )
    if tests:
        spec = root / "spec" / "example_spec.rb"
        spec.parent.mkdir(parents=True)
        spec.write_text("RSpec.describe Example do\nend\n", encoding="utf-8")
    return root


def test_ruby_router_clarifies_missing_path_and_routes_project() -> None:
    router = DeterministicRouter()

    missing = router.route("ruby verify")
    verify = router.route("verifica proyecto Ruby /tmp/ruby")
    inspect = router.route("inspecciona proyecto Ruby /tmp/ruby")

    assert missing.kind == "clarification"
    assert missing.params["intended_skill"] == "ruby.verify_project"
    assert verify.skill_name == "ruby.verify_project"
    assert inspect.skill_name == "ruby.project_inspect"


def test_ruby_inspect_reports_metadata_without_execution(
    isolated_home: ElyndraPaths,
) -> None:
    project = _ruby_project(Path.home() / "Proyectos" / "ruby-inspect")
    marker = project / "executed"
    _executable(project / "bin" / "bundle", f"#!/bin/sh\ntouch '{marker}'\n")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "ruby.project_inspect",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    inventory = result.data["inventory"]
    assert inventory["ruby_files"] == 2
    assert inventory["test_files"] == 1
    assert inventory["gemfile"] is True
    assert inventory["test_framework"] == "rspec"
    assert not marker.exists()


def test_ruby_external_project_requires_one_time_authorization(
    isolated_home: ElyndraPaths,
) -> None:
    project = _ruby_project(Path.home() / "Escritorio" / "ruby-external")
    app = ElyndraApplication.load(isolated_home)

    denied = app.execute_skill(
        "ruby.project_inspect",
        {"path": str(project)},
        approved=True,
    )
    allowed = app.execute_skill(
        "ruby.project_inspect",
        {"path": str(project), "allow_root_once": True},
        approved=True,
    )

    assert denied.ok is False
    assert "--allow-root-once" in denied.message
    assert allowed.ok is True
    assert allowed.data["authorization_scope"] == "project_once"


def test_ruby_descriptor_is_deterministic_and_does_not_evaluate_gemfile(
    isolated_home: ElyndraPaths,
) -> None:
    project = _ruby_project(Path.home() / "Proyectos" / "ruby-descriptor")
    marker = project / "gemfile-ran"
    with (project / "Gemfile").open("a", encoding="utf-8") as handle:
        handle.write(f"File.write('{marker}', 'bad')\n")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "ruby.descriptor_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["report"]["errors"] == []
    assert result.data["report"]["warnings"]
    assert not marker.exists()


def test_ruby_syntax_uses_fixed_argv_and_never_uses_shell(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _ruby_project(Path.home() / "Proyectos" / "ruby-syntax", tests=False)
    log = Path.home() / "ruby.log"
    _executable(
        project / ".venv" / "bin" / "ruby",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "ruby.syntax_check",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["shell"] is False
    assert result.data["files_examined"] == 1
    assert "-c" in log.read_text(encoding="utf-8")


def test_bundle_check_never_installs_or_updates(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _ruby_project(Path.home() / "Proyectos" / "ruby-bundle")
    log = Path.home() / "bundle.log"
    _executable(
        project / "bin" / "bundle",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "ruby.bundle_check",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["shell"] is False
    assert log.read_text(encoding="utf-8").strip() == "check"
    assert "install" not in str(result.data["command_argv"])


def test_rubocop_uses_project_binstub_without_autocorrect(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _ruby_project(Path.home() / "Proyectos" / "ruby-rubocop")
    log = Path.home() / "rubocop.log"
    _executable(
        project / "bin" / "rubocop",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "rubocop.check",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    command = log.read_text(encoding="utf-8")
    assert "--format simple" in command
    assert "--cache false" in command
    assert "autocorrect" not in command.casefold()


def test_rspec_uses_fixed_arguments(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _ruby_project(Path.home() / "Proyectos" / "ruby-rspec")
    log = Path.home() / "rspec.log"
    _executable(
        project / "bin" / "rspec",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "ruby.test_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["test_framework"] == "rspec"
    assert log.read_text(encoding="utf-8").strip() == "--format progress"


def test_ruby_verify_persists_partial_history_without_tools(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _ruby_project(Path.home() / "Proyectos" / "ruby-history")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "ruby.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    run = result.data["verification_run"]
    assert run["toolchain"] == "ruby"
    assert run["status"] == "partial"
    stages = {item["name"]: item for item in result.data["stages"]}
    assert stages["inspect"]["status"] == "passed"
    assert stages["descriptor"]["status"] == "passed"
    assert stages["bundle"]["status"] == "unavailable"


def test_ruby_verify_passes_with_controlled_fake_tools(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _ruby_project(Path.home() / "Proyectos" / "ruby-verify")
    for name in ("bundle", "rubocop", "rspec"):
        _executable(project / "bin" / name, "#!/bin/sh\nexit 0\n")
    _executable(project / ".venv" / "bin" / "ruby", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "ruby.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["verification_run"]["status"] == "passed"
    assert all(item["status"] == "passed" for item in result.data["stages"])


def test_ruby_profile_and_control_center(isolated_home: ElyndraPaths) -> None:
    project = _ruby_project(Path.home() / "Proyectos" / "ruby-profile")
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    profile = service.save_ruby_profile(
        {
            "project_root": str(project),
            "rubocop_enabled": False,
            "test_framework": "rspec",
            "max_ruby_files": 222,
        }
    )
    overview = service.control_overview()
    projects = service.control_projects()

    assert profile["rubocop_enabled"] is False
    assert profile["test_framework"] == "rspec"
    assert profile["max_ruby_files"] == 222
    assert overview["ruby_profiles"] == 1
    assert "ruby_verifications" in overview
    assert projects["ruby_profiles"][0]["project_root"] == str(project)
    with app.database.connect() as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert version == "51"


def test_ruby_knowledge_package_is_valid(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    package = Path(__file__).parents[1] / "knowledge-packs" / "ruby-modern-basic"

    inspected = app.alexandria_packages.inspect(package)

    assert inspected["package_id"] == "programming.ruby.modern-basic"
    assert inspected["domain"] == "programming/ruby"
    assert inspected["source_count"] == 1
