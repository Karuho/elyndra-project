from __future__ import annotations

from pathlib import Path

from elyndra.skills.process import run_controlled_process


def _script(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_controlled_process_times_out_and_bounds_output(tmp_path: Path) -> None:
    sleeper = _script(
        tmp_path / "sleeper",
        "#!/bin/sh\nprintf 'before-timeout\\n'\nsleep 5\n",
    )

    result = run_controlled_process(
        [str(sleeper)],
        cwd=tmp_path,
        timeout_seconds=1,
        max_output_chars=1_000,
    )

    assert result.timed_out is True
    assert result.returncode is not None
    assert "before-timeout" in result.stdout
    assert result.duration_ms < 4_000
    assert result.stdout_truncated is False
    assert result.stderr_truncated is False


def test_controlled_process_keeps_only_bounded_tail(tmp_path: Path) -> None:
    noisy = _script(
        tmp_path / "noisy",
        "#!/bin/sh\n"
        "i=0\n"
        "while [ $i -lt 500 ]; do\n"
        "  printf 'line-%04d-abcdefghij\\n' $i\n"
        "  i=$((i+1))\n"
        "done\n",
    )

    result = run_controlled_process(
        [str(noisy)],
        cwd=tmp_path,
        timeout_seconds=5,
        max_output_chars=1_000,
    )

    assert result.returncode == 0
    assert len(result.stdout) <= 1_030
    assert result.stdout.startswith("… salida truncada …")
    assert result.stdout_truncated is True
    assert result.stderr_truncated is False
    assert "line-0499" in result.stdout


def test_controlled_process_bounds_stderr_independently(tmp_path: Path) -> None:
    noisy = _script(
        tmp_path / "noisy-stderr",
        "#!/bin/sh\n"
        "i=0\n"
        "while [ $i -lt 500 ]; do\n"
        "  printf 'error-%04d-abcdefghij\\n' $i >&2\n"
        "  i=$((i+1))\n"
        "done\n",
    )

    result = run_controlled_process(
        [str(noisy)],
        cwd=tmp_path,
        timeout_seconds=5,
        max_output_chars=1_000,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr.startswith("… salida truncada …")
    assert result.stdout_truncated is False
    assert result.stderr_truncated is True
    assert "error-0499" in result.stderr
