from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Task, User, Prediction
from app.schemas import TaskCreate, TaskOut
from app.auth import get_current_user
from app.eventbus import publish
from app.recommend import score_task_priority

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.post("", response_model=TaskOut, status_code=201)
def create_task(req: TaskCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = Task(
        project_id=req.project_id, title=req.title, story_points=req.story_points,
        dependency_count=req.dependency_count, blocking_count=req.blocking_count,
        effort_hours=req.effort_hours, deadline=req.deadline,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    publish(db, "task.created", project_id=task.project_id, source="task-service", payload={"task_id": task.id})
    return task


@router.get("", response_model=List[TaskOut])
def list_tasks(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Task).filter(Task.project_id == project_id).order_by(Task.created_at.desc()).all()


@router.get("/{task_id}/priority")
def task_priority(task_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Pull the most recent real project-level defect/security signal available,
    # rather than inventing one, to feed the priority formula.
    preds = db.query(Prediction).filter(
        Prediction.project_id == task.project_id, Prediction.target_type == "module_defect_risk"
    ).order_by(Prediction.predicted_at.desc()).limit(20).all()
    avg_defect_risk = sum(p.probability for p in preds if p.probability is not None) / len(preds) if preds else 0.0

    result = score_task_priority(task, defect_risk=avg_defect_risk, security_risk=0.0)
    pred = Prediction(
        project_id=task.project_id, target_type="task_priority", target_ref=task.id,
        score=result["score"], model_name="weighted_priority_formula", model_version="v1",
        dataset_version="n/a", feature_schema_version="1.0",
        input_features=result["components"],
    )
    db.add(pred)
    db.commit()
    publish(db, "task.updated", project_id=task.project_id, source="task-service",
            payload={"task_id": task.id, "priority_score": result["score"]})
    return {"task_id": task.id, **result}


@router.patch("/{task_id}/status", response_model=TaskOut)
def update_status(task_id: str, status: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    import datetime as dt
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = status
    if status == "DONE":
        task.completed_at = dt.datetime.utcnow()
        publish(db, "task.completed", project_id=task.project_id, source="task-service", payload={"task_id": task.id})
    db.add(task)
    db.commit()
    return task
