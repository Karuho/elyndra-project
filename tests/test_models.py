from __future__ import annotations

import os
from pathlib import Path

from elyndra.application import ElyndraApplication
from elyndra.engines.llama_cli import LlamaCliEngine
from elyndra.models import PROFILES, LanguageConfig, discover_local_models, write_language_config
from elyndra.paths import ElyndraPaths


def _fake_llama_cli(path: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '--help' in sys.argv:\n"
        "    print('--threads-batch --parallel --cache-type-k --cache-type-v --prio --poll ')\n"
        "    print('--reasoning --reasoning-budget --conversation --single-turn --simple-io ')\n"
        "    print('--no-display-prompt --no-show-timings --no-warmup --color --system-prompt')\n"
        "elif '--version' in sys.argv:\n"
        "    print('fake llama.cpp 1.0')\n"
        "else:\n"
        "    print('Elyndra local funciona.')\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_language_config_round_trip(isolated_home: ElyndraPaths, tmp_path: Path) -> None:
    binary = _fake_llama_cli(tmp_path / "llama-cli")
    model = tmp_path / "tiny.gguf"
    model.write_bytes(b"GGUF")

    target = write_language_config(
        isolated_home,
        binary=binary,
        model=model,
        profile="eco",
    )
    config = LanguageConfig.load(isolated_home)

    assert target == isolated_home.language_config_file
    assert config.enabled is True
    assert config.binary == binary.resolve()
    assert config.model == model.resolve()
    assert config.profile == PROFILES["eco"]


def test_discovery_finds_runtime_and_model(
    tmp_path: Path, monkeypatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    binary = _fake_llama_cli(bin_dir / "llama-cli")
    model = tmp_path / "models" / "tiny.gguf"
    model.parent.mkdir()
    model.write_bytes(b"GGUF" * 256)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    report = discover_local_models((tmp_path,), max_files=100)

    assert any(item.path == str(binary.resolve()) for item in report.runtimes)
    assert any(item.path == str(model.resolve()) for item in report.models)
    assert report.truncated is False


def test_llama_cli_engine_returns_local_output(tmp_path: Path) -> None:
    binary = _fake_llama_cli(tmp_path / "llama-cli")
    model = tmp_path / "tiny.gguf"
    model.write_bytes(b"GGUF")
    config = LanguageConfig(True, "llama-cli", binary, model, PROFILES["eco"])
    engine = LlamaCliEngine(config, "Elyn", "Carlos")

    reply = engine.reply("Saluda", context=("MEMORIA: Elyndra es local",))

    assert reply.generated is True
    assert reply.text == "Elyndra local funciona."
    assert reply.engine.startswith("llama-cli:tiny.gguf")


def test_application_loads_configured_language_engine(
    isolated_home: ElyndraPaths, tmp_path: Path
) -> None:
    binary = _fake_llama_cli(tmp_path / "llama-cli")
    model = tmp_path / "tiny.gguf"
    model.write_bytes(b"GGUF")
    write_language_config(isolated_home, binary=binary, model=model, profile="eco")

    app = ElyndraApplication.load(isolated_home)
    result = app.ask("Dime algo nuevo")

    assert result.ok is True
    assert result.data["generated"] is True
    assert result.message == "Elyndra local funciona."


def test_invalid_language_config_does_not_break_core(isolated_home: ElyndraPaths) -> None:
    isolated_home.language_config_file.write_text(
        '[language]\nenabled = true\nbackend = "llama-cli"\n'
        'binary = "/missing/llama-cli"\nmodel = "/missing/model.gguf"\n'
        'profile = "eco"\n',
        encoding="utf-8",
    )

    app = ElyndraApplication.load(isolated_home)
    result = app.ask("Escribe algo no soportado")

    assert app.language_engine.name == "no-model:config-error"
    assert result.ok is True
    assert result.data["generated"] is False
