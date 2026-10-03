import os
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config import settings
from app.database import get_db
from app.models import EventLog, AuditLog, User
from app.eventbus import TOPICS
from app.auth import get_current_user

router = APIRouter(prefix="/api/v1/monitoring", tags=["monitoring"])


@router.get("/events")
def event_stream(project_id: str = None, limit: int = 100,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Real persisted event log (spec Section 73 Event Monitor)."""
    q = db.query(EventLog)
    if project_id:
        q = q.filter(EventLog.project_id == project_id)
    return q.order_by(EventLog.created_at.desc()).limit(limit).all()


@router.get("/events/throughput")
def event_throughput(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(EventLog.event_type, func.count(EventLog.event_id)).group_by(EventLog.event_type).all()
    return {"topics_configured": TOPICS, "counts": {t: c for t, c in rows}}


@router.get("/health")
def health():
    """Service health endpoint (no auth required, for load balancers/k8s probes)."""
    db_status = "UP" if settings.DATABASE_URL.startswith("postgres") else "SQLITE"
    github_status = "CONNECTED" if settings.GITHUB_TOKEN else "NOT CONFIGURED"
    jira_status = "CONFIGURED" if settings.JIRA_CLIENT_ID and settings.JIRA_CLIENT_SECRET else "NOT CONFIGURED"
    google_status = "CONFIGURED" if settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET else "NOT CONFIGURED"
    sonar_status = "CONNECTED" if settings.SONAR_URL and settings.SONAR_TOKEN else "NOT CONFIGURED"
    zap_status = "CONNECTED" if settings.ZAP_URL else "NOT CONFIGURED"
    ml_ready = os.path.exists(os.path.join(settings.MODEL_ARTIFACT_DIR, "champion_model.joblib"))

    return {
        "status": "UP",
        "service": "aetherai-backend",
        "database": db_status,
        "github": github_status,
        "jira": jira_status,
        "google": google_status,
        "sonar": sonar_status,
        "zap": zap_status,
        "ml": "READY" if ml_ready else "NOT READY",
    }


@router.get("/audit-log")
def audit_log(limit: int = 100, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role not in ("ADMIN",):
        return {"detail": "Admin role required to view full audit log"}
    return db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
