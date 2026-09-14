from __future__ import annotations

from pathlib import Path

import pytest

from elyndra.autonomy import PublicProjectMutationPolicy


def test_public_project_policy_allows_unrelated_workspace(tmp_path: Path) -> None:
    protected = tmp_path / "runtime" / "elyndra"
    workspace = tmp_path / "projects" / "client"
    protected.mkdir(parents=True)
    workspace.mkdir(parents=True)

    PublicProjectMutationPolicy((protected,)).require_allowed(workspace)


@pytest.mark.parametrize("relationship", ["exact", "nested", "containing"])
def test_public_project_policy_denies_every_product_overlap(
    tmp_path: Path, relationship: str
) -> None:
    protected = tmp_path / "runtime" / "elyndra"
    protected.mkdir(parents=True)
    workspace = {
        "exact": protected,
        "nested": protected / "examples" / "project",
        "containing": protected.parent,
    }[relationship]
    workspace.mkdir(parents=True, exist_ok=True)

    with pytest.raises(PermissionError, match="se superpone"):
        PublicProjectMutationPolicy((protected,)).require_allowed(workspace)


def test_public_project_policy_does_not_use_path_prefixes(tmp_path: Path) -> None:
    protected = tmp_path / "elyndra"
    lookalike = tmp_path / "elyndra-copy"
    protected.mkdir()
    lookalike.mkdir()

    PublicProjectMutationPolicy((protected,)).require_allowed(lookalike)


def test_public_project_policy_requires_real_canonical_directories(tmp_path: Path) -> None:
    protected = tmp_path / "runtime"
    protected.mkdir()
    with pytest.raises(ValueError):
        PublicProjectMutationPolicy(())
    with pytest.raises(ValueError):
        PublicProjectMutationPolicy((tmp_path / "missing",))
    with pytest.raises(ValueError):
        PublicProjectMutationPolicy((protected,)).require_allowed(tmp_path / "missing")


def test_public_project_policy_resolves_symlink_identity(tmp_path: Path) -> None:
    protected = tmp_path / "runtime"
    protected.mkdir()
    alias = tmp_path / "runtime-alias"
    alias.symlink_to(protected, target_is_directory=True)

    with pytest.raises(PermissionError, match="se superpone"):
        PublicProjectMutationPolicy((protected,)).require_allowed(alias)
