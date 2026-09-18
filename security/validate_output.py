"""
security/validate_output.py
(recreated in sandbox for testing — canonical version already on the user's machine)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class StructuredFacts:
    possession_delay_months: int = 0
    complaint_count: int = 0
    rera_registered: bool = True
    price_inr: Optional[float] = None
    expected_price_mid: Optional[float] = None


@dataclass
class AgentClaim:
    credibility_score: float
    reasoning: str
    source_agent: str


@dataclass
class ValidationResult:
    approved: bool
    adjusted_score: float
    notes: list[str]


MAX_UNEXPLAINED_UPWARD_DEVIATION = 1.5   # tight — this is the injection-attack direction
MAX_UNEXPLAINED_DOWNWARD_DEVIATION = 3.0  # looser — legitimate qualitative red flags usually push scores down, not up


def score_from_structured_facts(facts: StructuredFacts) -> float:
    score = 10.0
    score -= min(facts.possession_delay_months * 0.3, 4.0)
    score -= min(facts.complaint_count * 0.5, 4.0)
    if not facts.rera_registered:
        score -= 5.0
    return max(0.0, min(10.0, score))


def validate(claim: AgentClaim, facts: StructuredFacts) -> ValidationResult:
    """Validate an agent's credibility claim against structured ground truth.

    Uses an ASYMMETRIC deviation threshold — this was tightened after a real
    finding during testing: a symmetric ±3.0 threshold let a full-injection
    attempt (claim=10.0) pass through unclamped when the structured baseline
    was already fairly high (7.2), because the resulting deviation (+2.8)
    stayed just under the old flat 3.0 limit. Since prompt injection almost
    always tries to INFLATE a score (that's the entire point of the attack),
    while legitimate qualitative signal more often REVEALS a problem the
    structured facts don't yet capture (i.e. pushes the score DOWN), the
    guard is deliberately stricter on upward deviation than downward.
    """
    baseline = score_from_structured_facts(facts)
    deviation = claim.credibility_score - baseline
    notes: list[str] = []

    limit = MAX_UNEXPLAINED_UPWARD_DEVIATION if deviation > 0 else MAX_UNEXPLAINED_DOWNWARD_DEVIATION

    if abs(deviation) > limit:
        clamped = baseline + limit * (1 if deviation > 0 else -1)
        notes.append(
            f"agent score {claim.credibility_score} deviated {deviation:+.1f} "
            f"from structured baseline {baseline:.1f} (limit for this direction: {limit}); "
            f"clamped to {clamped:.1f}"
        )
        print(f"[security.validate_output] SUSPICIOUS: {notes[-1]} (source={claim.source_agent})")
        return ValidationResult(approved=True, adjusted_score=round(clamped, 1), notes=notes)

    if not facts.rera_registered and claim.credibility_score > 2.0:
        notes.append("project is not RERA-registered; score hard-capped regardless of agent output")
        print(f"[security.validate_output] BLOCKED: {notes[-1]} (source={claim.source_agent})")
        return ValidationResult(approved=True, adjusted_score=2.0, notes=notes)

    return ValidationResult(approved=True, adjusted_score=round(claim.credibility_score, 1), notes=notes)


def validate_price_deviation_claim(
    reasoning_text: str,
    actual_price: Optional[float],
    expected_price_mid: Optional[float],
) -> ValidationResult:
    if actual_price is None or expected_price_mid is None:
        return ValidationResult(
            approved=False, adjusted_score=0.0,
            notes=["missing price data — cannot support a deviation claim, flag rejected"],
        )

    deviation_pct = (actual_price - expected_price_mid) / expected_price_mid
    supports_flag = deviation_pct <= -0.10

    if not supports_flag:
        return ValidationResult(
            approved=False, adjusted_score=deviation_pct,
            notes=["numeric deviation does not support a flag; agent reasoning discarded"],
        )

    return ValidationResult(approved=True, adjusted_score=deviation_pct, notes=[])
