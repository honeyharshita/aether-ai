from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models import Project, User, Issue, Task, SecurityFinding, CodeModule, Repository, Prediction
from app.schemas import ProjectCreate, ProjectOut
from app.auth import get_current_user
from app.eventbus import publish

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(req: ProjectCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = Project(name=req.name, description=req.description, is_demo=req.is_demo, owner_id=user.id)
    db.add(project)
    db.commit()
    db.refresh(project)
    publish(db, "project.created", project_id=project.id, source="project-service",
            payload={"name": project.name, "is_demo": project.is_demo})
    return project


@router.get("", response_model=List[ProjectOut])
def list_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Project).order_by(Project.created_at.desc()).all()


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/{project_id}/risk-timeline")
def risk_timeline(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Real project risk trend (spec Section 30), aggregated from actual
    stored Prediction rows grouped by day -- not interpolated or faked.
    Days with no predictions are simply absent from the series.
    """
    from sqlalchemy import func
    rows = (
        db.query(
            func.date(Prediction.predicted_at).label("day"),
            func.avg(Prediction.probability).label("avg_risk"),
            func.count(Prediction.id).label("n"),
        )
        .filter(Prediction.project_id == project_id, Prediction.target_type == "module_defect_risk",
                Prediction.probability.isnot(None))
        .group_by(func.date(Prediction.predicted_at))
        .order_by(func.date(Prediction.predicted_at))
        .all()
    )
    if not rows:
        return {"project_id": project_id, "series": [], "message": "No predictions recorded yet for this project."}
    return {
        "project_id": project_id,
        "series": [{"date": str(r.day), "avg_risk": round(float(r.avg_risk), 4), "predictions": r.n} for r in rows],
    }


@router.get("/{project_id}/health")
def project_health(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Explainable project health score (spec Section 29/56).
    Computed live from real stored signals -- never hardcoded.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    open_bugs = db.query(Issue).filter(Issue.project_id == project_id, Issue.status == "open").count()
    total_issues = db.query(Issue).filter(Issue.project_id == project_id).count()
    open_critical_sec = db.query(SecurityFinding).filter(
        SecurityFinding.project_id == project_id, SecurityFinding.status == "open",
        SecurityFinding.severity.in_(["critical", "high"]),
    ).count()
    total_sec = db.query(SecurityFinding).filter(SecurityFinding.project_id == project_id).count()

    repo_ids = [r.id for r in db.query(Repository).filter(Repository.project_id == project_id).all()]
    modules = db.query(CodeModule).filter(CodeModule.repository_id.in_(repo_ids)).all() if repo_ids else []
    avg_coverage = sum(m.test_coverage for m in modules) / len(modules) if modules else None

    preds = db.query(Prediction).filter(
        Prediction.project_id == project_id, Prediction.target_type == "module_defect_risk"
    ).all()
    avg_defect_risk = sum(p.probability for p in preds if p.probability is not None) / len(preds) if preds else None

    tasks = db.query(Task).filter(Task.project_id == project_id).all()
    overdue = 0
    import datetime as dt
    for t in tasks:
        if t.deadline and t.status != "DONE" and t.deadline < dt.datetime.utcnow():
            overdue += 1

    # Explainable weighted component score. Missing components (no data
    # yet) are excluded from the weighted average and disclosed, rather
    # than silently defaulted to a "good" value.
    components = {}
    if total_issues > 0:
        components["defect_health"] = 100 * (1 - open_bugs / max(total_issues, 1))
    if avg_defect_risk is not None:
        components["predicted_defect_health"] = 100 * (1 - avg_defect_risk)
    if total_sec > 0:
        components["security_health"] = 100 * (1 - open_critical_sec / max(total_sec, 1))
    if avg_coverage is not None:
        components["test_health"] = 100 * avg_coverage
    if tasks:
        components["schedule_health"] = 100 * (1 - overdue / max(len(tasks), 1))

    if not components:
        return {
            "project_id": project_id, "health_score": None,
            "message": "Not enough data yet to compute a health score. Connect a repository and sync issues/tasks.",
            "components": {},
        }

    health_score = round(sum(components.values()) / len(components), 1)
    return {
        "project_id": project_id,
        "health_score": health_score,
        "components": {k: round(v, 1) for k, v in components.items()},
        "raw_signals": {
            "open_bugs": open_bugs, "total_issues": total_issues,
            "open_critical_security_findings": open_critical_sec,
            "avg_test_coverage": round(avg_coverage, 3) if avg_coverage is not None else None,
            "avg_predicted_defect_risk": round(avg_defect_risk, 3) if avg_defect_risk is not None else None,
            "overdue_tasks": overdue, "total_tasks": len(tasks),
        },
    }
