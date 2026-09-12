from __future__ import annotations

from typing import Any

import pytest

from elyndra.application import ElyndraApplication
from elyndra.identity import OwnerIdentity


class _LoopSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            if name.startswith("list_"):
                return []
            if name in {"owner_wait", "successor_handoff"}:
                return None
            return {"public_id": "result", "status": "ok"}

        return call


def _application(loop: Any) -> ElyndraApplication:
    app = object.__new__(ElyndraApplication)
    app.identity = OwnerIdentity("Owner", "owner")
    app.cognitive_loop = loop
    return app


def test_application_exposes_exact_phase8b_operations_without_business_logic() -> None:
    loop = _LoopSpy()
    app = _application(loop)
    plan = object()

    assert app.list_cognitive_owner_waits() == []
    assert app.cognitive_owner_wait("wait") is None
    assert app.list_cognitive_successor_handoffs() == []
    assert app.cognitive_successor_handoff("handoff") is None
    app.continue_cognitive_wait_with_context("wait", context="owner context")
    app.retry_cognitive_reasoning("wait")
    app.continue_cognitive_wait_after_ordinary_gate("wait")
    app.continue_cognitive_wait_after_retry_review("wait", "review", "gate")
    app.continue_cognitive_wait_without_replan("wait")
    app.continue_abandoned_cognitive_action("wait")
    app.stop_cognitive_wait("wait")
    app.cancel_cognitive_wait("wait")
    app.propose_cognitive_successor(
        "wait",
        request_key="request",
        objective="objective",
        workspace_root="/workspace",
        plan=plan,  # type: ignore[arg-type]
        grant_spec={"capabilities": ["process.exec"]},
    )
    app.reject_cognitive_successor("handoff")
    app.accept_cognitive_successor("handoff")

    assert [call[0] for call in loop.calls] == [
        "list_owner_waits",
        "owner_wait",
        "list_successor_handoffs",
        "successor_handoff",
        "continue_with_context",
        "retry_reasoning",
        "continue_after_ordinary_gate",
        "continue_after_retry_review",
        "continue_without_replan",
        "continue_abandoned_action",
        "stop_wait",
        "cancel_wait",
        "propose_successor",
        "reject_successor",
        "accept_successor",
    ]
    assert all(call[2]["actor"] == "owner" for call in loop.calls)
    retry = loop.calls[7]
    assert retry[1] == ("wait", "review", "gate")


def test_application_phase8b_surface_requires_a_vault_loop() -> None:
    app = _application(None)

    with pytest.raises(PermissionError, match="bóveda"):
        app.list_cognitive_owner_waits()
