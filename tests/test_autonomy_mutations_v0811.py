from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from elyndra.autonomy import (
    MAX_ORIGINAL_BYTES,
    MAX_PROPOSAL_LIFETIME,
    MAX_PROPOSED_BYTES_PER_ITEM,
    MAX_TOTAL_PROPOSED_BYTES,
    MUTATION_PROPOSAL_DOMAIN,
    MutationItem,
    MutationOperation,
    MutationProposal,
)

_CREATED = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
_ORIGINAL_SHA = hashlib.sha256(b"old\n").hexdigest()


def _create(path: str = "src/new.py", content: bytes | str = b"new\n") -> MutationItem:
    return MutationItem(
        relative_path=path,
        operation=MutationOperation.CREATE,
        original_exists=False,
        original_sha256=None,
        original_size=None,
        proposed_content=content,
    )


def _replace(
    path: str = "src/existing.py",
    content: bytes | str = b"new\n",
    *,
    original_sha256: str = _ORIGINAL_SHA,
    original_size: int = 4,
) -> MutationItem:
    return MutationItem(
        relative_path=path,
        operation=MutationOperation.REPLACE,
        original_exists=True,
        original_sha256=original_sha256,
        original_size=original_size,
        proposed_content=content,
    )


def _proposal(*items: MutationItem, **overrides: object) -> MutationProposal:
    values: dict[str, object] = {
        "run_id": "run-1",
        "step_id": "modify-1",
        "actor": "owner",
        "workspace_root": "/work/project",
        "items": items or (_create(),),
        "created_at": _CREATED,
        "expires_at": _CREATED + timedelta(minutes=30),
    }
    values.update(overrides)
    return MutationProposal(**values)  # type: ignore[arg-type]


def test_proposal_sha256_is_deterministic_and_domain_separated() -> None:
    first = _proposal(_replace(), _create())
    second = _proposal(_create(), _replace())

    assert first.proposal_sha256 == second.proposal_sha256
    assert re.fullmatch(r"[0-9a-f]{64}", first.proposal_sha256)
    assert MUTATION_PROPOSAL_DOMAIN == "elyndra.autonomy.mutation-proposal.v1"
    expected = hashlib.sha256(
        MUTATION_PROPOSAL_DOMAIN.encode("utf-8")
        + b"\0"
        + first.canonical_json_bytes()
    ).hexdigest()
    assert first.proposal_sha256 == expected


@pytest.mark.parametrize(
    "changed",
    [
        _proposal(_create("src/other.py")),
        _proposal(_replace("src/new.py")),
        _proposal(_replace(original_sha256="1" * 64)),
        _proposal(_create(content=b"different\n")),
        _proposal(workspace_root="/work/other"),
        _proposal(run_id="run-2"),
        _proposal(step_id="modify-2"),
        _proposal(actor="another-owner"),
        _proposal(
            created_at=_CREATED + timedelta(seconds=1),
            expires_at=_CREATED + timedelta(minutes=30),
        ),
        _proposal(expires_at=_CREATED + timedelta(minutes=29)),
    ],
    ids=[
        "path",
        "operation",
        "preimage-hash",
        "proposed-bytes",
        "workspace",
        "run",
        "step",
        "actor",
        "created",
        "expires",
    ],
)
def test_authoritative_field_changes_proposal_hash(changed: MutationProposal) -> None:
    assert changed.proposal_sha256 != _proposal().proposal_sha256


def test_items_are_stored_in_deterministic_utf8_path_order() -> None:
    proposal = _proposal(_create("z.py"), _create("a.py"))

    assert [item.relative_path for item in proposal.items] == ["a.py", "z.py"]


def test_duplicate_paths_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicadas"):
        _proposal(_create("same.py"), _create("same.py"))


def test_non_nfc_path_is_rejected_instead_of_normalized() -> None:
    with pytest.raises(ValueError, match="NFC"):
        _create("src/cafe\u0301.py")


def test_casefold_path_collision_is_rejected() -> None:
    with pytest.raises(ValueError, match="casefold"):
        _proposal(_create("README"), _create("readme"))


@pytest.mark.parametrize(
    "path",
    [
        "/absolute.py",
        "//host/path.py",
        "../escape.py",
        "src/../escape.py",
        "./source.py",
        "src/./source.py",
        "src\\source.py",
        "src//source.py",
        "src/",
        "",
    ],
)
def test_unsafe_or_noncanonical_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        _create(path)


@pytest.mark.parametrize(
    "path",
    [
        ".git/config",
        "src/.hg/store",
        ".svn/entries",
        ".github/workflows/ci.yml",
        ".gitlab/pipeline.yml",
        ".circleci/config.yml",
        ".devcontainer/devcontainer.json",
        ".vscode/settings.json",
        ".elyndra-mutation-journal/attempt",
        "secrets.json",
        "certificates/private.pem",
    ],
)
def test_protected_namespaces_and_secret_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValueError, match="proteg"):
        _create(path)


@pytest.mark.parametrize(
    ("exists", "sha256", "size"),
    [
        (True, None, None),
        (False, _ORIGINAL_SHA, None),
        (False, None, 0),
    ],
)
def test_create_invariants_are_enforced(
    exists: bool, sha256: str | None, size: int | None
) -> None:
    with pytest.raises(ValueError, match="CREATE"):
        MutationItem("new.py", MutationOperation.CREATE, exists, sha256, size, b"")


@pytest.mark.parametrize(
    ("exists", "sha256", "size", "error"),
    [
        (False, _ORIGINAL_SHA, 4, ValueError),
        (True, None, 4, ValueError),
        (True, _ORIGINAL_SHA, None, TypeError),
        (True, _ORIGINAL_SHA, -1, ValueError),
    ],
)
def test_replace_invariants_are_enforced(
    exists: bool,
    sha256: str | None,
    size: int | None,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        MutationItem("old.py", MutationOperation.REPLACE, exists, sha256, size, b"")


@pytest.mark.parametrize("sha256", ["", "a" * 63, "a" * 65, "G" * 64, "A" * 64])
def test_invalid_or_nonlowercase_sha256_is_rejected(sha256: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        _replace(original_sha256=sha256)


@pytest.mark.parametrize("content", [b"\xff", b"prefix\x00suffix", "prefix\x00suffix"])
def test_invalid_utf8_and_nul_content_are_rejected(content: bytes | str) -> None:
    with pytest.raises(ValueError):
        _create(content=content)


@pytest.mark.parametrize(
    "content",
    [
        "-----BEGIN " + "PRIVATE KEY-----",
        "-----BEGIN " + "RSA PRIVATE KEY-----",
        "-----BEGIN " + "EC PRIVATE KEY-----",
        "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
        "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0",
        "AKIA" + "ABCDEFGHIJKLMNOP",
    ],
    ids=[
        "generic-private-key",
        "rsa-private-key",
        "ec-private-key",
        "openssh-private-key",
        "github-token",
        "aws-access-key-id",
    ],
)
def test_obvious_secret_material_is_rejected_without_echo(content: str) -> None:
    with pytest.raises(ValueError) as captured:
        _create(content=content)

    assert content not in str(captured.value)


def test_short_github_looking_and_ordinary_security_words_are_accepted() -> None:
    content = (
        "secret = 'not-a-secret'\n"
        "token = 'ghp_short'\n"
        "private = True\n"
        "provider = 'AKIA'\n"
    )

    assert _create(content=content).proposed_content == content.encode("utf-8")


def test_secret_rejection_does_not_mutate_supplied_bytes() -> None:
    content = ("prefix\r\n-----BEGIN " + "PRIVATE KEY-----\ncafe\u0301").encode()
    original = bytes(content)

    with pytest.raises(ValueError, match="material secreto"):
        _create(content=content)

    assert content == original


def test_empty_content_is_allowed() -> None:
    item = _create(content=b"")

    assert item.proposed_content == b""
    assert item.proposed_size == 0


def test_per_file_proposed_byte_limit_is_enforced() -> None:
    assert _create(content=b"a" * MAX_PROPOSED_BYTES_PER_ITEM).proposed_size == 65_536
    with pytest.raises(ValueError, match="proposed_content"):
        _create(content=b"a" * (MAX_PROPOSED_BYTES_PER_ITEM + 1))


def test_total_proposed_byte_limit_is_enforced() -> None:
    with pytest.raises(ValueError, match="límite total"):
        _proposal(
            _create("a.py", b"a" * MAX_PROPOSED_BYTES_PER_ITEM),
            _create("b.py", b"b" * MAX_PROPOSED_BYTES_PER_ITEM),
            _create("c.py", b"c"),
        )
    assert MAX_TOTAL_PROPOSED_BYTES == 131_072


def test_three_item_limit_is_enforced() -> None:
    _proposal(_create("a.py"), _create("b.py"), _create("c.py"))
    with pytest.raises(ValueError, match="entre 1 y 3"):
        _proposal(
            _create("a.py"),
            _create("b.py"),
            _create("c.py"),
            _create("d.py"),
        )


def test_existing_preimage_size_limit_is_enforced() -> None:
    assert _replace(original_size=MAX_ORIGINAL_BYTES).original_size == 262_144
    with pytest.raises(ValueError, match="original_size"):
        _replace(original_size=MAX_ORIGINAL_BYTES + 1)


def test_path_and_component_utf8_byte_limits_are_enforced() -> None:
    valid = "a" * 255
    assert _create(valid).relative_path == valid
    with pytest.raises(ValueError, match="componente"):
        _create("a" * 256)
    with pytest.raises(ValueError, match="ruta supera"):
        _create("/".join(("a" * 200, "b" * 200, "c" * 111)))


def test_naive_or_non_utc_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError, match="zona horaria"):
        _proposal(created_at=datetime(2026, 9, 12, 12), expires_at=_CREATED)
    plus_one = timezone(timedelta(hours=1))
    with pytest.raises(ValueError, match="UTC"):
        _proposal(
            created_at=datetime(2026, 9, 12, 13, tzinfo=plus_one),
            expires_at=datetime(2026, 9, 12, 13, 10, tzinfo=plus_one),
        )


def test_lifetime_must_be_positive_and_at_most_thirty_minutes() -> None:
    assert _proposal().expires_at - _proposal().created_at == MAX_PROPOSAL_LIFETIME
    with pytest.raises(ValueError, match="posterior"):
        _proposal(expires_at=_CREATED)
    with pytest.raises(ValueError, match="30 minutos"):
        _proposal(expires_at=_CREATED + timedelta(minutes=30, microseconds=1))


def test_exact_utf8_bytes_are_preserved_without_normalization() -> None:
    content = b"cafe\xcc\x81\r\nnext\n"
    item = _create(content=content)

    assert item.proposed_content is content
    assert item.proposed_size == len(content)
    assert item.proposed_sha256 == hashlib.sha256(content).hexdigest()
    assert b"Y2FmZcyB\r\nbmV4dAo=" not in _proposal(item).canonical_json_bytes()
    assert item.canonical_data()["proposed_content_base64"] == "Y2FmZcyBDQpuZXh0Cg=="


def test_lookup_or_request_ids_are_not_modeled_or_hashed() -> None:
    proposal = _proposal()

    assert "proposal_id" not in proposal.canonical_data()
    assert "request_key" not in proposal.canonical_data()
    assert not hasattr(proposal, "proposal_id")
    assert not hasattr(proposal, "request_key")


def test_immutable_domain_objects_cannot_be_changed() -> None:
    proposal = _proposal()

    with pytest.raises(AttributeError):
        proposal.actor = "attacker"  # type: ignore[misc]
    assert replace(proposal, actor="owner-2").proposal_sha256 != proposal.proposal_sha256
