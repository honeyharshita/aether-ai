import json
import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Prediction, Explanation, ModelRegistryEntry, User, CodeModule, Repository, Commit, FusionWeightHistory
from app.auth import get_current_user
from app.config import settings
from app.ml.explain import predict_defect_probability
from app.ml.recalibrate import recalibrate_weights
from app.recommend import RULES
from app.eventbus import publish
from app.risk_propagation import cochange_evidence, propagate_risk, MIN_COOCCURRENCE

router = APIRouter(prefix="/api/v1", tags=["predictions"])


class SimulationRequest(BaseModel):
    project_id: str
    module_path: str
    loc: float | None = Field(default=None, ge=0)
    cyclomatic_complexity: float | None = Field(default=None, ge=0)
    num_functions: float | None = Field(default=None, ge=0)
    churn_30d: float | None = Field(default=None, ge=0)
    num_commits: float | None = Field(default=None, ge=0)
    num_authors: float | None = Field(default=None, ge=0)
    historical_defects: float | None = Field(default=None, ge=0)
    test_coverage: float | None = Field(default=None, ge=0, le=1)


@router.get("/predictions")
def list_predictions(project_id: str, target_type: str = None,
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Prediction).filter(Prediction.project_id == project_id)
    if target_type:
        q = q.filter(Prediction.target_type == target_type)
    return q.order_by(Prediction.predicted_at.desc()).limit(200).all()


@router.get("/explanations/{prediction_id}")
def get_explanation(prediction_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    expl = db.query(Explanation).filter(Explanation.prediction_id == prediction_id).first()
    if not expl:
        raise HTTPException(status_code=404, detail="No explanation found for this prediction")
    return expl


@router.get("/code-risk")
def code_risk_view(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Per-module risk view (spec Section 31): latest prediction + module metrics side by side."""
    repo_ids = [r.id for r in db.query(Repository).filter(Repository.project_id == project_id).all()]
    modules = db.query(CodeModule).filter(CodeModule.repository_id.in_(repo_ids)).all() if repo_ids else []

    result = []
    for m in modules:
        latest_pred = db.query(Prediction).filter(
            Prediction.project_id == project_id, Prediction.target_type == "module_defect_risk",
            Prediction.target_ref == m.path,
        ).order_by(Prediction.predicted_at.desc()).first()
        result.append({
            "path": m.path, "loc": m.loc, "cyclomatic_complexity": m.cyclomatic_complexity,
            "num_functions": m.num_functions, "historical_defects": m.historical_defects,
            "churn_30d": m.churn_30d, "num_commits": m.num_commits, "num_authors": m.num_authors,
            "test_coverage": m.test_coverage, "historical_defects": m.historical_defects,
            "defect_probability": latest_pred.probability if latest_pred else None,
            "prediction_id": latest_pred.id if latest_pred else None,
        })
    return sorted(result, key=lambda r: (r["defect_probability"] or 0), reverse=True)


@router.get("/code-risk/{module_path:path}/blast-radius")
def module_blast_radius(module_path: str, project_id: str,
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo_ids = [repo.id for repo in db.query(Repository).filter(Repository.project_id == project_id).all()]
    if not repo_ids:
        return {"module_path": module_path, "seed_risk": None, "threshold": 0.5, "results": []}
    modules = db.query(CodeModule).filter(CodeModule.repository_id.in_(repo_ids)).all()
    seed_modules = [module for module in modules if module.path == module_path]
    if not seed_modules:
        raise HTTPException(status_code=404, detail="Analyzed module not found in this project")

    module_by_repo_path = {}
    module_by_id = {}
    for module in modules:
        module_by_repo_path.setdefault((module.repository_id, module.path), []).append(module.id)
        module_by_id[module.id] = module
    module_ids = list(module_by_id)
    latest_predictions = db.query(Prediction).filter(
        Prediction.project_id == project_id,
        Prediction.target_type == "module_defect_risk",
        Prediction.target_ref == module_path,
    ).order_by(Prediction.predicted_at.desc()).all()
    seed_risk = latest_predictions[0].probability if latest_predictions else None
    seed = seed_modules[0]

    commits = db.query(Commit).filter(Commit.repository_id.in_(repo_ids)).order_by(Commit.committed_at.desc()).all()
    evidence = cochange_evidence(commits, module_by_repo_path)
    propagated = propagate_risk(seed.id, seed_risk, evidence)
    results = []
    for item in propagated:
        module = module_by_id[item["module_id"]]
        results.append({
            "module_path": module.path,
            "repository_id": module.repository_id,
            "propagated_risk_contribution": item["propagated_risk_contribution"],
            "cochange_count": item["cochange_count"],
            "recent_joint_commits": item["recent_joint_commits"],
            "commit_window": 12,
            "hops": item["hops"],
            "evidence": f"changed together in {item['recent_joint_commits']} of the last 12 commits; {item['cochange_count']} total co-changes",
        })
    return {
        "module_path": module_path,
        "seed_risk": seed_risk,
        "threshold": 0.5,
        "minimum_cooccurrence": MIN_COOCCURRENCE,
        "results": results,
    }


@router.post("/predictions/simulate")
def simulate_prediction(req: SimulationRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo_ids = [repo.id for repo in db.query(Repository).filter(Repository.project_id == req.project_id).all()]
    module = db.query(CodeModule).filter(
        CodeModule.repository_id.in_(repo_ids), CodeModule.path == req.module_path,
    ).first() if repo_ids else None
    if not module:
        raise HTTPException(status_code=404, detail="Analyzed module not found in this project")

    features = {
        "loc": module.loc,
        "cyclomatic_complexity": module.cyclomatic_complexity,
        "num_functions": module.num_functions,
        "churn_30d": module.churn_30d,
        "num_commits": module.num_commits,
        "num_authors": module.num_authors,
        "historical_defects": module.historical_defects,
        "test_coverage": module.test_coverage,
    }
    for feature in features:
        override = getattr(req, feature)
        if override is not None:
            features[feature] = override

    try:
        result = predict_defect_probability(features)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="Trained champion model is unavailable") from exc
    return {
        "is_simulation": True,
        "label": "hypothetical - not a real observation",
        "project_id": req.project_id,
        "module_path": req.module_path,
        "features": features,
        **result,
    }


@router.get("/models/registry")
def model_registry(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(ModelRegistryEntry).order_by(ModelRegistryEntry.trained_at.desc()).all()


@router.get("/anomalies")
def module_anomalies(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Real autoencoder-based anomaly detection (spec Section 10, Model 7)
    over every analyzed module's current metrics.
    """
    from app.ml.anomaly import score_anomaly
    repo_ids = [r.id for r in db.query(Repository).filter(Repository.project_id == project_id).all()]
    modules = db.query(CodeModule).filter(CodeModule.repository_id.in_(repo_ids)).all() if repo_ids else []

    autoencoder_path = os.path.join(settings.MODEL_ARTIFACT_DIR, "autoencoder.joblib")
    if not os.path.exists(autoencoder_path):
        raise HTTPException(status_code=404, detail="Autoencoder not trained yet. Run `python -m app.ml.anomaly`.")

    results = []
    for m in modules:
        features = {
            "loc": m.loc, "cyclomatic_complexity": m.cyclomatic_complexity,
            "num_functions": m.num_functions, "churn_30d": m.churn_30d,
            "num_commits": m.num_commits, "num_authors": m.num_authors,
            "historical_defects": m.historical_defects, "test_coverage": m.test_coverage,
        }
        result = score_anomaly(features)
        results.append({"path": m.path, **result})
    return sorted(results, key=lambda r: r["reconstruction_error"], reverse=True)


@router.get("/models/training-manifest")
def training_manifest(user: User = Depends(get_current_user)):
    """Real training run results for the Model Comparison UI (spec Section 36)."""
    path = os.path.join(settings.MODEL_ARTIFACT_DIR, "training_manifest.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="No training run has been executed yet. Run `python -m app.ml.train`.")
    with open(path) as f:
        return json.load(f)


@router.get("/models/fusion-weights")
def fusion_weights(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    history = db.query(FusionWeightHistory).order_by(FusionWeightHistory.version.desc()).all()
    current = history[0] if history else None
    default_weights = {rule["action"]: 1.0 for rule in RULES}
    return {
        "current_version": current.version if current else 0,
        "weights": current.weights if current else default_weights,
        "metrics": current.metrics if current else {},
        "sample_size": current.sample_size if current else 0,
        "history": [
            {
                "version": row.version,
                "weights": row.weights,
                "metrics": row.metrics,
                "sample_size": row.sample_size,
                "created_at": row.created_at,
            }
            for row in reversed(history)
        ],
    }


@router.post("/models/recalibrate")
def recalibrate_models(minimum_samples: int = 5,
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if minimum_samples < 5 or minimum_samples > 1000:
        raise HTTPException(status_code=422, detail="minimum_samples must be between 5 and 1000")
    try:
        weights, metrics, sample_size = recalibrate_weights(db, minimum_samples=minimum_samples)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    latest = db.query(FusionWeightHistory).order_by(FusionWeightHistory.version.desc()).first()
    version = (latest.version if latest else 0) + 1
    record = FusionWeightHistory(
        version=version, weights=weights, metrics=metrics, sample_size=sample_size,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    publish(db, "model.fusion_weights.recalibrated", source="recommendation-service", payload={
        "version": version, "sample_size": sample_size,
    })
    return {
        "version": version,
        "weights": weights,
        "metrics": metrics,
        "sample_size": sample_size,
        "created_at": record.created_at,
    }
