from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models import Issue, User
from app.schemas import IssueCreate, IssueOut
from app.auth import get_current_user
from app.eventbus import publish

router = APIRouter(prefix="/api/v1/issues", tags=["issues"])


@router.post("", response_model=IssueOut, status_code=201)
def create_issue(req: IssueCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    issue = Issue(
        project_id=req.project_id, title=req.title, issue_type=req.issue_type,
        severity=req.severity, related_module_path=req.related_module_path,
    )
    db.add(issue)
    db.commit()
    db.refresh(issue)
    publish(db, "issue.created", project_id=issue.project_id, source="issue-service",
            payload={"issue_id": issue.id, "severity": issue.severity})
    return issue


@router.get("", response_model=List[IssueOut])
def list_issues(project_id: str, status: str = None,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Issue).filter(Issue.project_id == project_id)
    if status:
        q = q.filter(Issue.status == status)
    return q.order_by(Issue.created_at.desc()).all()


@router.patch("/{issue_id}/close", response_model=IssueOut)
def close_issue(issue_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    import datetime as dt
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    issue.status = "closed"
    issue.resolved_at = dt.datetime.utcnow()
    db.add(issue)
    db.commit()
    publish(db, "issue.closed", project_id=issue.project_id, source="issue-service", payload={"issue_id": issue.id})
    return issue
