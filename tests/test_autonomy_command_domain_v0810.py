from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from elyndra.autonomy.commands import (
    CommandEnvironmentProfile,
    CommandSandboxProfile,
    CommandSnapshot,
    CommandSpec,
    CommandStdinPolicy,
    ExecutableIdentity,
    valid_command_sha256,
)


def _python_executable() -> str:
    return str(Path(sys.executable).resolve(strict=True))


def _spec(
    executable: str | None = None,
    *,
    cwd: str = ".",
    timeout_seconds: int = 30,
) -> CommandSpec:
    resolved = executable or _python_executable()

    return CommandSpec(
        executable=resolved,
        argv=(
            resolved,
            "-c",
            "print('elyndra-command-domain')",
        ),
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )


def test_command_spec_round_trip_is_deterministic() -> None:
    spec = _spec()

    rebuilt = CommandSpec.from_data(spec.to_data())

    assert rebuilt == spec
    assert rebuilt.argv[0] == rebuilt.executable
    assert (
        rebuilt.sandbox_profile
        is CommandSandboxProfile.WORKSPACE_RW_NO_NETWORK_V1
    )
    assert (
        rebuilt.environment_profile
        is CommandEnvironmentProfile.MINIMAL_V1
    )
    assert rebuilt.stdin_policy is CommandStdinPolicy.DEVNULL


def test_command_spec_requires_exact_absolute_executable() -> None:
    with pytest.raises(ValueError, match="ruta absoluta"):
        CommandSpec(
            executable="python3",
            argv=("python3",),
        )

    with pytest.raises(ValueError, match="normalizado"):
        CommandSpec(
            executable="/usr/bin/../bin/python3",
            argv=("/usr/bin/../bin/python3",),
        )


def test_command_spec_requires_argv_zero_to_match_executable() -> None:
    executable = _python_executable()

    with pytest.raises(ValueError, match=r"argv\[0\]"):
        CommandSpec(
            executable=executable,
            argv=("/usr/bin/not-the-authorized-binary",),
        )


def test_command_spec_rejects_unsafe_cwd_forms() -> None:
    executable = _python_executable()

    with pytest.raises(ValueError, match="relativo"):
        CommandSpec(
            executable=executable,
            argv=(executable,),
            cwd="/tmp",
        )

    with pytest.raises(ValueError, match=r"\.\."):
        CommandSpec(
            executable=executable,
            argv=(executable,),
            cwd="../outside",
        )


def test_command_spec_bounds_timeout_and_output() -> None:
    executable = _python_executable()

    with pytest.raises(ValueError, match="timeout_seconds"):
        CommandSpec(
            executable=executable,
            argv=(executable,),
            timeout_seconds=901,
        )

    with pytest.raises(ValueError, match="stdout_limit_bytes"):
        CommandSpec(
            executable=executable,
            argv=(executable,),
            stdout_limit_bytes=1_048_577,
        )


def test_executable_identity_captures_canonical_elf() -> None:
    executable = _python_executable()

    identity = ExecutableIdentity.capture(executable)

    assert identity.canonical_path == executable
    assert identity.size > 0
    assert valid_command_sha256(identity.sha256)


def test_command_snapshot_is_stable_when_filesystem_is_unchanged(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    snapshot = CommandSnapshot.capture(
        _spec(),
        resolved_cwd=root,
    )

    current = snapshot.revalidate()

    assert current.command_sha256 == snapshot.command_sha256
    assert valid_command_sha256(snapshot.command_sha256)


def test_executable_symlink_is_rejected_even_if_target_is_valid(
    tmp_path: Path,
) -> None:
    target = Path(_python_executable())
    alias = tmp_path / "python-alias"
    alias.symlink_to(target)

    with pytest.raises(PermissionError, match="canónica exacta"):
        ExecutableIdentity.capture(alias)


def test_command_snapshot_detects_executable_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    executable = tmp_path / "python-copy"
    shutil.copy2(_python_executable(), executable)
    executable.chmod(0o755)

    spec = _spec(str(executable))

    snapshot = CommandSnapshot.capture(
        spec,
        resolved_cwd=root,
    )

    with executable.open("ab") as handle:
        handle.write(b"\x00")

    with pytest.raises(
        PermissionError,
        match="cambió",
    ):
        snapshot.revalidate()