from __future__ import annotations

from pathlib import Path

import elyndra


def test_release_version_and_public_docs_are_synchronized() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert elyndra.__version__ == "0.8.10-alpha"
    assert 'version = "0.8.10-alpha"' in pyproject
    assert "Status: `0.8.10-alpha`" in readme
    assert (root / "docs" / "RELEASE_0.7.25.md").is_file()
    assert (root / "docs" / "RELEASE_0.7.28.md").is_file()
    assert (root / "docs" / "RELEASE_0.7.31.md").is_file()
    assert (root / "docs" / "RELEASE_0.7.32.md").is_file()
    assert (root / "docs" / "RELEASE_0.8.0.md").is_file()
    assert (root / "docs" / "RELEASE_0.8.1.md").is_file()
    assert (root / "docs" / "RELEASE_0.8.2.md").is_file()
    assert (root / "docs" / "RELEASE_0.8.3.md").is_file()
    assert (root / "docs" / "RELEASE_0.8.4.md").is_file()
    assert (root / "docs" / "RELEASE_0.8.5.md").is_file()
    assert (root / "docs" / "RELEASE_0.8.6.md").is_file()
    assert (root / "docs" / "RELEASE_0.8.7.md").is_file()
    assert (root / "docs" / "IDENTITY_AND_DIALOGUE.md").is_file()
    assert (root / "docs" / "SEMANTIC_UNDERSTANDING.md").is_file()
    assert (root / "docs" / "LOCAL_SCHEDULER.md").is_file()
    assert (root / "docs" / "POLICY_BOUNDED_AUTOMATION.md").is_file()
    assert (root / "docs" / "COGNITIVE_EXECUTIVE.md").is_file()
    assert (root / "docs" / "PERSONAL_ORGANIZER.md").is_file()
    assert (root / "docs" / "PERSONAL_COACHING.md").is_file()
    assert (root / "docs" / "INTERFACE_PARITY.md").is_file()
    assert (root / "docs" / "TUTOR_LEARNING.md").is_file()
    assert (root / "docs" / "TUTOR_EVOLUTION.md").is_file()


def test_changelog_backfills_missing_release_line() -> None:
    changelog = (Path(__file__).resolve().parents[1] / "CHANGELOG.md").read_text(
        encoding="utf-8"
    )
    expected = (
        "0.3.5-dev",
        "0.4.0-dev",
        "0.4.1-dev",
        "0.4.2-dev",
        "0.5.0-dev",
        "0.5.1-dev",
        "0.5.2-dev",
        "0.5.3-dev",
        "0.5.4-dev",
        "0.6.0-dev",
        "0.6.1-dev",
        "0.6.2-dev",
        "0.6.3-dev",
        "0.7.0-dev",
        "0.7.1-dev",
        "0.7.2-dev",
        "0.7.3-dev",
        "0.7.4-dev",
        "0.7.5-dev",
        "0.7.6-dev",
        "0.7.7-dev",
        "0.7.8-dev",
        "0.7.9-dev",
        "0.7.10-dev",
        "0.7.11-dev",
        "0.7.12-alpha",
        "0.7.13-alpha",
        "0.7.14-alpha",
        "0.7.15-alpha",
        "0.7.16-alpha",
        "0.7.17-alpha",
        "0.7.18-alpha",
        "0.7.19-alpha",
        "0.7.20-alpha",
        "0.7.21-alpha",
        "0.7.22-alpha",
        "0.7.23-alpha",
        "0.7.24-alpha",
        "0.7.25-alpha",
        "0.7.26-alpha",
        "0.7.27-alpha",
        "0.7.28-alpha",
        "0.7.29-alpha",
        "0.7.30-alpha",
        "0.7.31-alpha",
        "0.7.32-alpha",
        "0.8.0-alpha",
        "0.8.1-alpha",
        "0.8.2-alpha",
        "0.8.3-alpha",
        "0.8.4-alpha",
        "0.8.5-alpha",
        "0.8.6-alpha",
        "0.8.8-alpha",
        "0.8.9-alpha",
    )

    for version in expected:
        assert f"## {version}" in changelog


def test_python_release_note_and_shipped_package_are_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.6.md"
    package_manifest = (
        root / "knowledge-packs" / "python-modern-basic" / "elyndra-package.json"
    )

    assert release_note.is_file()
    assert "controlled Python project toolchain" in release_note.read_text(encoding="utf-8")
    assert package_manifest.is_file()


def test_java_release_note_and_shipped_package_are_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.7.md"
    package_manifest = (
        root / "knowledge-packs" / "java-modern-basic" / "elyndra-package.json"
    )

    assert release_note.is_file()
    assert "controlled Java/JVM project toolchain" in release_note.read_text(
        encoding="utf-8"
    )
    assert package_manifest.is_file()


def test_native_release_note_and_shipped_package_are_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.8.md"
    package_manifest = (
        root / "knowledge-packs" / "c-cpp-modern-basic" / "elyndra-package.json"
    )

    assert release_note.is_file()
    assert "controlled C and C++ toolchain" in release_note.read_text(
        encoding="utf-8"
    )
    assert package_manifest.is_file()


def test_ruby_release_note_and_shipped_package_are_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.9.md"
    package_manifest = (
        root / "knowledge-packs" / "ruby-modern-basic" / "elyndra-package.json"
    )

    assert release_note.is_file()
    assert "controlled Ruby project toolchain" in release_note.read_text(
        encoding="utf-8"
    )
    assert package_manifest.is_file()


def test_go_release_note_and_shipped_package_are_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.10.md"
    package_manifest = (
        root / "knowledge-packs" / "go-modern-basic" / "elyndra-package.json"
    )

    assert release_note.is_file()
    assert "controlled Go project toolchain" in release_note.read_text(
        encoding="utf-8"
    )
    assert package_manifest.is_file()


def test_rust_release_note_and_shipped_package_are_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.11.md"
    package_manifest = (
        root / "knowledge-packs" / "rust-modern-basic" / "elyndra-package.json"
    )

    assert release_note.is_file()
    assert "controlled Rust project toolchain" in release_note.read_text(
        encoding="utf-8"
    )
    assert package_manifest.is_file()


def test_dotnet_release_note_and_shipped_package_are_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.13.md"
    package_manifest = (
        root / "knowledge-packs" / "dotnet-modern-basic" / "elyndra-package.json"
    )

    assert release_note.is_file()
    assert "Controlled C#/.NET project toolchain" in release_note.read_text(
        encoding="utf-8"
    )
    assert package_manifest.is_file()


def test_swift_release_note_and_shipped_package_are_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.14.md"
    package_manifest = (
        root / "knowledge-packs" / "swift-modern-basic" / "elyndra-package.json"
    )

    assert release_note.is_file()
    assert "Controlled Swift project toolchain" in release_note.read_text(
        encoding="utf-8"
    )
    assert package_manifest.is_file()


def test_dart_release_note_and_shipped_package_are_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.15.md"
    package_manifest = (
        root
        / "knowledge-packs"
        / "dart-flutter-modern-basic"
        / "elyndra-package.json"
    )

    assert release_note.is_file()
    assert "Controlled Dart and Flutter project toolchain" in release_note.read_text(
        encoding="utf-8"
    )
    assert package_manifest.is_file()


def test_sql_release_note_and_shipped_package_are_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.16.md"
    package_manifest = (
        root
        / "knowledge-packs"
        / "sql-databases-modern-basic"
        / "elyndra-package.json"
    )

    assert release_note.is_file()
    assert "Controlled SQL and database toolchain" in release_note.read_text(
        encoding="utf-8"
    )
    assert package_manifest.is_file()


def test_supervised_orchestration_release_is_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.17.md"

    assert release_note.is_file()
    text = release_note.read_text(encoding="utf-8")
    assert "Supervised assistant orchestration" in text
    assert "four" in text
    assert "single-use" in text


def test_controlled_change_proposal_release_is_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.18.md"

    assert release_note.is_file()
    text = release_note.read_text(encoding="utf-8")
    assert "Controlled change proposals and reviewable patches" in text
    assert "single-use" in text
    assert "assistant_change_proposals" in text


def test_supervised_validation_repair_release_is_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.19.md"

    assert release_note.is_file()
    text = release_note.read_text(encoding="utf-8")
    assert "Supervised validation and repair cycles" in text
    assert "assistant_validation_cycles" in text
    assert "No autonomous loop" in text


def test_supervised_development_sessions_release_is_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.20.md"
    content = release_note.read_text(encoding="utf-8")
    assert "Supervised development sessions" in content
    assert "assistant_development_sessions" in content
    assert "/api/control/development-sessions" in content
    assert "do not" in content or "does not" in content


def test_conversational_session_continuity_release_is_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.21.md"

    assert release_note.is_file()
    content = release_note.read_text(encoding="utf-8")
    assert "Conversational development-session continuity" in content
    assert "assistant_chat_session_focus" in content
    assert "never executes" in content


def test_constitutional_ethics_release_is_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.22.md"
    constitution = root / "docs" / "ETHICAL_CONSTITUTION.md"

    assert release_note.is_file()
    content = release_note.read_text(encoding="utf-8")
    assert "Immutable professional ethics constitution" in content
    assert "assistant_ethics_reviews" in content
    assert "/api/control/ethics" in content
    assert constitution.is_file()
    constitution_text = constitution.read_text(encoding="utf-8")
    assert "cannot disable" in constitution_text
    assert "authorized shutdown" in constitution_text


def test_ethics_v2_and_dictionary_release_is_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.23.md"
    dictionary_data = root / "src" / "elyndra" / "resources" / "dictionary_core_v1.json"

    assert release_note.is_file()
    content = release_note.read_text(encoding="utf-8")
    assert "Ethics review v2" in content
    assert "secondary local tutor review" in content
    assert "offline multilingual starter lexicon" in content
    assert "101 skills" in content
    assert "schema 31" in content
    assert dictionary_data.is_file()


def test_ethics_v3_first_aid_and_tiered_memory_release_is_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.24.md"
    first_aid = root / "src" / "elyndra" / "resources" / "first_aid_core_v1.json"

    assert release_note.is_file()
    content = release_note.read_text(encoding="utf-8")
    assert "Ethics review v3" in content
    assert "first-aid" in content.casefold()
    assert "hot, warm and cold" in content
    assert "102 skills" in content
    assert "schema 32" in content
    assert first_aid.is_file()


def test_structured_language_and_first_aid_packs_release_is_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release_note = root / "docs" / "RELEASE_0.7.25.md"
    dictionary_docs = root / "docs" / "DICTIONARY_PACKS.md"
    first_aid_docs = root / "docs" / "FIRST_AID_LIBRARY.md"

    assert release_note.is_file()
    content = release_note.read_text(encoding="utf-8")
    assert "Structured language" in content
    assert "first-aid" in content.casefold()
    assert "schema: `33`" in content
    assert "Skills: `102`" in content
    assert dictionary_docs.is_file()
    assert first_aid_docs.is_file()

def test_personal_coaching_and_interface_parity_are_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    release = (root / "docs" / "RELEASE_0.8.2.md").read_text(encoding="utf-8")
    coaching = (root / "docs" / "PERSONAL_COACHING.md").read_text(encoding="utf-8")
    parity = (root / "docs" / "INTERFACE_PARITY.md").read_text(encoding="utf-8")
    security = (root / "SECURITY.md").read_text(encoding="utf-8")
    contributing = (root / "CONTRIBUTING.md").read_text(encoding="utf-8")

    assert "Personal coaching, wellbeing and interface parity" in release
    assert "does not infer a diagnosis" in coaching
    assert "ElyndraApplication.ask" in parity
    assert "X-Elyndra-Version" in security
    assert "real loopback" in contributing
    assert "CHANGELOG.md" in contributing
