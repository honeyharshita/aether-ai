from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SecurityFinding, User
from app.auth import get_current_user
from app.eventbus import publish

router = APIRouter(prefix="/api/v1/security", tags=["security"])


@router.get("/findings")
def list_findings(project_id: str, severity: str = None,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(SecurityFinding).filter(SecurityFinding.project_id == project_id)
    if severity:
        q = q.filter(SecurityFinding.severity == severity)
    return q.order_by(SecurityFinding.detected_at.desc()).all()


@router.post("/findings/import")
def import_findings(project_id: str, findings: list,
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Ingest real findings from a security scanner report (SonarQube/ZAP/
    Trivy JSON export). Each item: {source, severity, cve, component}.
    """
    created = []
    for f in findings:
        row = SecurityFinding(
            project_id=project_id, source=f.get("source", "unknown"),
            severity=f.get("severity", "medium"), cve=f.get("cve"),
            component=f.get("component"), status="open",
        )
        db.add(row)
        created.append(row)
    db.commit()
    publish(db, "security.scan.completed", project_id=project_id, source="security-service",
            payload={"findings_imported": len(created)})
    return {"imported": len(created)}
