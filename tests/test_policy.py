from elyndra.policy import PolicyEngine, RiskLevel


def test_low_risk_is_automatic() -> None:
    assert PolicyEngine().evaluate(RiskLevel.LOW).allowed is True


def test_medium_risk_requires_approval() -> None:
    engine = PolicyEngine()
    assert engine.evaluate(RiskLevel.MEDIUM).allowed is False
    assert engine.evaluate(RiskLevel.MEDIUM, approved=True).allowed is True


def test_high_risk_is_blocked_even_if_approved() -> None:
    assert PolicyEngine().evaluate(RiskLevel.HIGH, approved=True).allowed is False
