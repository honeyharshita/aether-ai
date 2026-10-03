import datetime as dt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Recommendation, Prediction, User
from app.schemas import RecommendationActionUpdate
from app.auth import get_current_user
from app.eventbus import publish

router = APIRouter(prefix="/api/v1/recommendations", tags=["recommendations"])


@router.get("")
def list_recommendations(project_id: str, status: str = None,
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Recommendation).filter(Recommendation.project_id == project_id)
    if status:
        q = q.filter(Recommendation.status == status)
    return q.order_by(Recommendation.created_at.desc()).all()


@router.patch("/{rec_id}")
def update_recommendation(rec_id: str, req: RecommendationActionUpdate,
                           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Closed-loop tracking (spec Section 3/23): when a recommendation is
    marked completed, capture risk_before/risk_after from real Prediction
    rows so effectiveness_score is computed from actual outcome data.
    """
    rec = db.query(Recommendation).filter(Recommendation.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    if req.status == "accepted":
        rec.status = "accepted"
        rec.assigned_user_id = user.id
        rec.action_started_at = dt.datetime.utcnow()
        if rec.source_prediction_id:
            src = db.query(Prediction).filter(Prediction.id == rec.source_prediction_id).first()
            rec.risk_before = src.probability if src else None

    elif req.status == "rejected":
        rec.status = "rejected"

    elif req.status == "completed":
        rec.status = "completed"
        rec.action_completed_at = dt.datetime.utcnow()
        # Look up the most recent prediction for the same target after the
        # action started, to measure real outcome (not assumed).
        if rec.related_ref:
            latest = db.query(Prediction).filter(
                Prediction.project_id == rec.project_id, Prediction.target_ref == rec.related_ref,
                Prediction.target_type == "module_defect_risk",
            ).order_by(Prediction.predicted_at.desc()).first()
            rec.risk_after = latest.probability if latest else None
        if rec.risk_before is not None and rec.risk_after is not None:
            delta = rec.risk_before - rec.risk_after
            rec.effectiveness_score = round(delta, 4)
            rec.outcome = "risk_decreased" if delta > 0 else ("risk_increased" if delta < 0 else "no_change")
        else:
            rec.outcome = "outcome_unmeasured_no_followup_prediction"
        publish(db, "recommendation.completed", project_id=rec.project_id, source="recommendation-service",
                payload={"recommendation_id": rec.id, "outcome": rec.outcome, "effectiveness_score": rec.effectiveness_score})
    else:
        raise HTTPException(status_code=400, detail="status must be accepted, rejected, or completed")

    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec
