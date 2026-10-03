import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import AuditLog, EventLog, Prediction, Project, Recommendation, User

router = APIRouter(prefix="/api/v1/memory", tags=["engineering-memory"])


def _date_bound(value, end=False):
    if value is None:
        return None
    if value.time() == dt.time.min and end:
        return value + dt.timedelta(days=1) - dt.timedelta(microseconds=1)
    return value


def _prediction_summary(current, previous):
    target = current.target_ref or current.target_type
    risk = current.probability
    if risk is None:
        return f"A {current.target_type} prediction was recorded for {target}."
    if not previous or previous.probability is None:
        return f"Initial recorded defect risk for {target}: {risk:.0%}."

    old_risk = previous.probability
    direction = "rose" if risk > old_risk else "fell" if risk < old_risk else "remained unchanged"
    summary = f"Risk for {target} {direction} from {old_risk:.0%} to {risk:.0%}."
    old_churn = (previous.input_features or {}).get("churn_30d")
    new_churn = (current.input_features or {}).get("churn_30d")
    if old_churn is not None and new_churn is not None and old_churn != new_churn:
        churn_direction = "increased" if new_churn > old_churn else "decreased"
        summary += f" Recorded churn {churn_direction} from {old_churn} to {new_churn}."
    return summary


def _event_summary(event):
    payload = event.payload or {}
    names = {
        "project.created": "A project was created.",
        "repository.connected": "Repository {full_name} was connected.",
        "repository.synced": "Repository {full_name} was synchronized.",
        "code.analysis.completed": "Code analysis completed for {modules_analyzed} modules.",
        "model.prediction.created": "A defect-risk prediction was recorded for {module}.",
        "recommendation.created": "Recommendation {action} was created for {module}.",
        "recommendation.completed": "Recommendation outcome recorded: {outcome}.",
        "issue.created": "Issue {title} was created.",
        "issue.closed": "Issue {title} was closed.",
        "task.created": "Task {title} was created.",
        "task.completed": "Task {title} was completed.",
    }
    template = names.get(event.event_type)
    if template:
        return template.format_map(_SafeFormat(payload))
    return f"Event {event.event_type} was recorded by {event.source}."


class _SafeFormat(dict):
    def __missing__(self, key):
        return "an item"


@router.get("/timeline")
def memory_timeline(
    project_id: str,
    from_date: dt.datetime | None = Query(default=None, alias="from"),
    to_date: dt.datetime | None = Query(default=None, alias="to"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Project not found")
    start = _date_bound(from_date)
    end = _date_bound(to_date, end=True)
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="The from date must not be after the to date")

    predictions_query = db.query(Prediction).filter(
        Prediction.project_id == project_id,
        Prediction.target_type == "module_defect_risk",
    )
    if end:
        predictions_query = predictions_query.filter(Prediction.predicted_at <= end)
    predictions = predictions_query.order_by(Prediction.predicted_at.asc()).all()
    previous_by_target = {}
    entries = []
    for prediction in predictions:
        previous = previous_by_target.get(prediction.target_ref)
        if (start is None or prediction.predicted_at >= start) and (end is None or prediction.predicted_at <= end):
            entries.append({
                "timestamp": prediction.predicted_at,
                "kind": "prediction",
                "source_id": prediction.id,
                "summary": _prediction_summary(prediction, previous),
                "evidence": {
                    "target_ref": prediction.target_ref,
                    "risk": prediction.probability,
                    "previous_risk": previous.probability if previous else None,
                    "features": prediction.input_features or {},
                },
            })
        previous_by_target[prediction.target_ref] = prediction

    recommendation_query = db.query(Recommendation).filter(Recommendation.project_id == project_id)
    if start:
        recommendation_query = recommendation_query.filter(Recommendation.created_at >= start)
    if end:
        recommendation_query = recommendation_query.filter(Recommendation.created_at <= end)
    for recommendation in recommendation_query.all():
        outcome = recommendation.outcome.replace("_", " ") if recommendation.outcome else "not yet measured"
        if recommendation.status == "completed":
            summary = f"Recommendation '{recommendation.recommended_action}' was completed; outcome: {outcome}."
            if recommendation.risk_before is not None and recommendation.risk_after is not None:
                summary += f" Recorded risk changed from {recommendation.risk_before:.0%} to {recommendation.risk_after:.0%}."
        else:
            summary = f"Recommendation '{recommendation.recommended_action}' is {recommendation.status}."
        entries.append({
            "timestamp": recommendation.action_completed_at or recommendation.action_started_at or recommendation.created_at,
            "kind": "recommendation",
            "source_id": recommendation.id,
            "summary": summary,
            "evidence": {
                "status": recommendation.status,
                "outcome": recommendation.outcome,
                "risk_before": recommendation.risk_before,
                "risk_after": recommendation.risk_after,
                "effectiveness_score": recommendation.effectiveness_score,
                "related_ref": recommendation.related_ref,
            },
        })

    event_query = db.query(EventLog).filter(EventLog.project_id == project_id)
    if start:
        event_query = event_query.filter(EventLog.created_at >= start)
    if end:
        event_query = event_query.filter(EventLog.created_at <= end)
    for event in event_query.all():
        if event.event_type == "recommendation.completed":
            continue
        entries.append({
            "timestamp": event.created_at,
            "kind": "event",
            "source_id": event.event_id,
            "summary": _event_summary(event),
            "evidence": {"event_type": event.event_type, "source": event.source, "payload": event.payload or {}},
        })

    audit_query = db.query(AuditLog)
    if start:
        audit_query = audit_query.filter(AuditLog.created_at >= start)
    if end:
        audit_query = audit_query.filter(AuditLog.created_at <= end)
    for audit in audit_query.all():
        detail = audit.detail or {}
        if detail.get("project_id") != project_id:
            continue
        entries.append({
            "timestamp": audit.created_at,
            "kind": "audit",
            "source_id": audit.id,
            "summary": f"Audit action {audit.action} was recorded.",
            "evidence": {"action": audit.action, "detail": detail},
        })

    entries.sort(key=lambda entry: entry["timestamp"] or dt.datetime.min)
    return {
        "project_id": project_id,
        "from": start,
        "to": end,
        "count": len(entries),
        "timeline": entries,
    }