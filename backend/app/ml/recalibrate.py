"""Calibrate recommendation ranking weights from measured outcomes only."""
from collections import defaultdict

from app.models import Recommendation
from app.recommend import RULES

MEASURED_OUTCOMES = {"risk_decreased", "risk_increased", "no_change"}


def recalibrate_weights(db, minimum_samples=5):
    actions = [rule["action"] for rule in RULES]
    measured = db.query(Recommendation).filter(
        Recommendation.status == "completed",
        Recommendation.outcome.in_(MEASURED_OUTCOMES),
    ).all()
    if len(measured) < minimum_samples:
        raise ValueError(
            f"At least {minimum_samples} completed recommendations with measured outcomes are required; "
            f"found {len(measured)}."
        )

    all_recommendations = db.query(Recommendation).filter(
        Recommendation.recommended_action.in_(actions)
    ).all()
    measured_ids = {recommendation.id for recommendation in measured}
    totals = defaultdict(int)
    accepted = defaultdict(int)
    outcomes = defaultdict(lambda: {"risk_decreased": 0, "risk_increased": 0, "no_change": 0})
    for recommendation in all_recommendations:
        action = recommendation.recommended_action
        totals[action] += 1
        if recommendation.status in {"accepted", "completed"}:
            accepted[action] += 1
        if recommendation.id in measured_ids:
            outcomes[action][recommendation.outcome] += 1

    weights = {}
    metrics = {}
    for action in actions:
        sample_size = sum(outcomes[action].values())
        if not sample_size:
            weights[action] = 1.0
            metrics[action] = {
                **outcomes[action], "sample_size": 0, "acceptance_rate": None,
                "effectiveness_rate": None,
            }
            continue
        acceptance_rate = accepted[action] / max(totals[action], 1)
        effectiveness_rate = (
            outcomes[action]["risk_decreased"] + 0.5 * outcomes[action]["no_change"]
        ) / sample_size
        weights[action] = round(0.5 + acceptance_rate * effectiveness_rate, 4)
        metrics[action] = {
            **outcomes[action],
            "sample_size": sample_size,
            "acceptance_rate": round(acceptance_rate, 4),
            "effectiveness_rate": round(effectiveness_rate, 4),
        }
    return weights, metrics, len(measured)