"""
Recommendation engine (spec Section 22).

Recommendations are derived purely from real prediction output + real
evidence rows already in the database (module metrics, security findings,
test runs, task fields) -- never fabricated independently of a
prediction. Each rule below states the condition, the evidence it reads,
and the action it proposes, matching spec Section 22's example rule set.
"""
from app.models import Recommendation, SecurityFinding, CodeModule, Task, FusionWeightHistory


RULES = [
    dict(
        condition=lambda ctx: ctx["defect_probability"] is not None and ctx["defect_probability"] >= 0.7,
        action="Review module and add targeted unit tests",
        reason_template="Defect risk is high ({pct}%), driven mainly by {top_factor}.",
        priority="high",
        role="DEVELOPER",
    ),
    dict(
        condition=lambda ctx: ctx["module"] and ctx["module"].cyclomatic_complexity >= 15,
        action="Refactor to reduce cyclomatic complexity",
        reason_template="Cyclomatic complexity is {complexity}, above the maintainability threshold.",
        priority="medium",
        role="DEVELOPER",
    ),
    dict(
        condition=lambda ctx: ctx["module"] and ctx["module"].test_coverage < 0.5,
        action="Add regression/unit tests to raise coverage",
        reason_template="Test coverage is {coverage}%, below the 50% target for this module.",
        priority="medium",
        role="QA_ENGINEER",
    ),
    dict(
        condition=lambda ctx: ctx["critical_security_findings"] > 0,
        action="Remediate critical security vulnerability immediately",
        reason_template="{count} critical/high security finding(s) are open for this project.",
        priority="critical",
        role="SECURITY_ENGINEER",
    ),
    dict(
        condition=lambda ctx: ctx["module"] and ctx["module"].churn_30d >= 20,
        action="Schedule focused code review for this module",
        reason_template="Recent churn is {churn} changed lines in the last analysis window, well above baseline.",
        priority="medium",
        role="DEVELOPER",
    ),
]


def evaluate_module_recommendations(db, project_id: str, module: CodeModule, prediction, explanation=None):
    """
    Given a real Prediction (+ optional Explanation) for a code module,
    evaluate the rule set and persist any recommendations that fire.
    Returns the list of created Recommendation rows.
    """
    critical_count = db.query(SecurityFinding).filter(
        SecurityFinding.project_id == project_id,
        SecurityFinding.status == "open",
        SecurityFinding.severity.in_(["critical", "high"]),
    ).count()

    top_factor = "unknown"
    if explanation and explanation.top_positive_factors:
        top_factor = explanation.top_positive_factors[0]["feature"]

    ctx = {
        "defect_probability": prediction.probability,
        "module": module,
        "critical_security_findings": critical_count,
        "top_factor": top_factor,
    }
    current_weights = db.query(FusionWeightHistory).order_by(FusionWeightHistory.version.desc()).first()
    rule_weights = current_weights.weights if current_weights else {}

    created = []
    for rule in RULES:
        if not rule["condition"](ctx):
            continue
        reason = rule["reason_template"].format(
            pct=round((prediction.probability or 0) * 100, 1),
            top_factor=top_factor,
            complexity=round(module.cyclomatic_complexity, 1) if module else None,
            coverage=round((module.test_coverage or 0) * 100, 1) if module else None,
            churn=module.churn_30d if module else None,
            count=critical_count,
        )
        baseline_priority = {"low": 1.0, "medium": 2.0, "high": 3.0, "critical": 4.0}[rule["priority"]]
        calibrated_priority = baseline_priority * rule_weights.get(rule["action"], 1.0)
        priority = "critical" if calibrated_priority >= 3.5 else (
            "high" if calibrated_priority >= 2.5 else "medium" if calibrated_priority >= 1.5 else "low"
        )
        rec = Recommendation(
            project_id=project_id,
            source_prediction_id=prediction.id,
            recommended_action=rule["action"],
            reason=reason,
            evidence={
                "module_path": module.path if module else None,
                "defect_probability": prediction.probability,
                "cyclomatic_complexity": module.cyclomatic_complexity if module else None,
                "test_coverage": module.test_coverage if module else None,
                "churn_30d": module.churn_30d if module else None,
                "critical_security_findings": critical_count,
            },
            priority=priority,
            responsible_role=rule["role"],
            related_ref=module.path if module else None,
            status="pending",
        )
        db.add(rec)
        created.append(rec)
    if created:
        db.commit()
        for r in created:
            db.refresh(r)
    return created


def score_task_priority(task: Task, defect_risk: float = 0.0, security_risk: float = 0.0) -> dict:
    """
    Real, explainable 0-100 task priority score (spec Section 13).
    Deterministic weighted formula over real task fields + real upstream
    risk predictions -- not a black box, and not random.
    """
    import datetime as dt

    days_to_deadline = None
    deadline_factor = 0.0
    if task.deadline:
        days_to_deadline = (task.deadline - dt.datetime.utcnow()).days
        deadline_factor = max(0.0, 1 - max(days_to_deadline, 0) / 30)  # closer deadline -> higher

    dependency_factor = min(task.blocking_count / 5, 1.0)
    age_days = (dt.datetime.utcnow() - task.created_at).days if task.created_at else 0
    age_factor = min(age_days / 30, 1.0)

    weights = {
        "deadline": 0.30,
        "dependency": 0.20,
        "defect_risk": 0.20,
        "security_risk": 0.20,
        "age": 0.10,
    }
    raw = (
        weights["deadline"] * deadline_factor
        + weights["dependency"] * dependency_factor
        + weights["defect_risk"] * defect_risk
        + weights["security_risk"] * security_risk
        + weights["age"] * age_factor
    )
    score = round(raw * 100, 1)

    reasons = []
    if deadline_factor > 0.5:
        reasons.append("deadline approaching")
    if dependency_factor > 0.3:
        reasons.append(f"blocks {task.blocking_count} downstream task(s)")
    if defect_risk > 0.5:
        reasons.append("high predicted defect risk")
    if security_risk > 0.5:
        reasons.append("elevated security risk")
    if age_factor > 0.6:
        reasons.append("task has been open a long time")

    return {
        "score": score,
        "components": {
            "deadline_factor": round(deadline_factor, 3),
            "dependency_factor": round(dependency_factor, 3),
            "defect_risk_factor": round(defect_risk, 3),
            "security_risk_factor": round(security_risk, 3),
            "age_factor": round(age_factor, 3),
        },
        "reasons": reasons,
    }
