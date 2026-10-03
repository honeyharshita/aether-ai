import datetime as dt
import re
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models import Repository, Commit, CodeModule, Project, User, Prediction, Explanation
from app.schemas import RepositoryConnect, RepositoryOut
from app.auth import get_current_user
from app.eventbus import publish
from app.github_ingest import fetch_repo_metadata, fetch_recent_commits, fetch_repo_tree_files, GitHubUnavailableError
from app.code_metrics import aggregate_module_metrics
from app.ml.explain import predict_defect_probability
from app.recommend import evaluate_module_recommendations

router = APIRouter(prefix="/api/v1/repositories", tags=["repositories"])

_GITHUB_URL_PATTERNS = [
    r"^(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/([^/\s.#?]+)(?:\.git)?(?:[/#?].*)?$",
    r"^git@github\.com:([^/\s]+)/([^/\s#?]+?)(?:\.git)?$",
]


def normalize_github_full_name(raw: str) -> str:
    """
    Accepts any of: 'owner/repo', 'https://github.com/owner/repo',
    'https://github.com/owner/repo.git', 'https://github.com/owner/repo/tree/main',
    'git@github.com:owner/repo.git' -- and returns the plain 'owner/repo'
    form the GitHub API expects. Raises ValueError with a clear message on
    anything unrecognizable, rather than silently passing a bad string to
    the API and surfacing a confusing 404 later.
    """
    raw = raw.strip()
    for pattern in _GITHUB_URL_PATTERNS:
        m = re.match(pattern, raw)
        if m:
            return f"{m.group(1)}/{m.group(2)}"
    if re.match(r"^[^/\s]+/[^/\s]+$", raw):
        return raw
    raise ValueError(
        "Could not parse a GitHub repository from that input. "
        "Paste either 'owner/repo' or a full URL like https://github.com/owner/repo"
    )


@router.post("", response_model=RepositoryOut, status_code=201)
def connect_repository(req: RepositoryConnect, user: User = Depends(get_current_user), db: Session = Depends(get_db),
                       github_token: str = Header(default=None, alias="X-GitHub-Token")):
    project = db.query(Project).filter(Project.id == req.project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    full_name = req.full_name
    default_branch = "main"
    if req.provider == "github":
        try:
            full_name = normalize_github_full_name(req.full_name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        try:
            meta = fetch_repo_metadata(full_name, token=github_token)
            default_branch = meta.get("default_branch", "main")
        except GitHubUnavailableError as e:
            raise HTTPException(status_code=502, detail=f"Repository synchronization temporarily unavailable: {e}")

    repo = Repository(project_id=req.project_id, provider=req.provider,
                       full_name=full_name, default_branch=default_branch)
    db.add(repo)
    db.commit()
    db.refresh(repo)
    publish(db, "repository.connected", project_id=project.id, source="repository-service",
            payload={"repository_id": repo.id, "full_name": repo.full_name})
    return repo


@router.get("", response_model=List[RepositoryOut])
def list_repositories(project_id: str = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Repository)
    if project_id:
        q = q.filter(Repository.project_id == project_id)
    return q.all()


class ConnectAndSyncRequest(RepositoryConnect):
    project_id: str = None  # if omitted, a new project is created from the repo name
    max_files: int = 20


@router.post("/connect-and-sync")
def connect_and_sync(req: ConnectAndSyncRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db),
                     github_token: str = Header(default=None, alias="X-GitHub-Token")):
    """
    One-shot flow for the dashboard's "paste a GitHub link" box: normalize
    the URL, create a project if none was given, connect the repository
    (or reuse it if already connected), then immediately run the full real
    sync (commits -> code analysis -> ML prediction -> SHAP explanation ->
    recommendations). Returns everything the UI needs to render results
    without a second round trip.
    """
    try:
        full_name = normalize_github_full_name(req.full_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        meta = fetch_repo_metadata(full_name, token=github_token)
    except GitHubUnavailableError as e:
        raise HTTPException(status_code=502, detail=f"Repository synchronization temporarily unavailable: {e}")

    project = None
    if req.project_id:
        project = db.query(Project).filter(Project.id == req.project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
    if not project:
        project = Project(
            name=meta.get("full_name", full_name),
            description=meta.get("description") or "",
            is_demo=False, owner_id=user.id,
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        publish(db, "project.created", project_id=project.id, source="project-service",
                payload={"name": project.name, "from_github": full_name})

    repo = db.query(Repository).filter(
        Repository.project_id == project.id, Repository.full_name == full_name
    ).first()
    if not repo:
        repo = Repository(project_id=project.id, provider="github", full_name=full_name,
                           default_branch=meta.get("default_branch", "main"))
        db.add(repo)
        db.commit()
        db.refresh(repo)
        publish(db, "repository.connected", project_id=project.id, source="repository-service",
                payload={"repository_id": repo.id, "full_name": repo.full_name})

    sync_result = _sync_repository_impl(repo, db, max_files=req.max_files, github_token=github_token)

    return {
        "project": {"id": project.id, "name": project.name, "is_demo": project.is_demo},
        "repository": {"id": repo.id, "full_name": repo.full_name, "default_branch": repo.default_branch},
        "github_metadata": {
            "stars": meta.get("stargazers_count"), "forks": meta.get("forks_count"),
            "open_issues": meta.get("open_issues_count"), "language": meta.get("language"),
            "description": meta.get("description"),
        },
        **sync_result,
    }


def _sync_repository_impl(repo: Repository, db: Session, max_files: int = 20, github_token: str = None) -> dict:
    """
    The end-to-end real-time demo flow (spec Section 46), executed
    synchronously here (a production deployment would trigger this from a
    GitHub webhook and process it async via the event bus, which is why
    every stage below still publishes its topic event to EventLog):

    GitHub sync -> commits.created -> code.analysis.completed ->
    model.prediction.created -> explanation computed -> recommendation.created

    Shared by both POST /{repository_id}/sync and POST /connect-and-sync
    so the "paste a link" flow and the "re-sync an existing repo" flow run
    identical real logic.
    """
    if repo.provider != "github":
        raise HTTPException(status_code=400, detail="Only github provider sync is implemented in this demo")

    try:
        commits = fetch_recent_commits(repo.full_name, per_page=30, token=github_token)
        files = fetch_repo_tree_files(repo.full_name, repo.default_branch, max_files=max_files, token=github_token)
    except GitHubUnavailableError as e:
        raise HTTPException(status_code=502, detail=f"Repository synchronization temporarily unavailable: {e}")

    if not files:
        raise HTTPException(
            status_code=422,
            detail=f"No analyzable source files found in {repo.full_name} "
                   f"(supported extensions: .py .js .ts .java .go .rb .c .cpp .jsx .tsx). "
                   f"The repository was connected, but there's nothing to analyze yet.",
        )

    # Persist real commits
    existing_shas = {c.sha for c in db.query(Commit).filter(Commit.repository_id == repo.id).all()}
    new_commits = 0
    for c in commits:
        if c["sha"] in existing_shas:
            continue
        committed_at = None
        if c.get("committed_at"):
            try:
                committed_at = dt.datetime.fromisoformat(c["committed_at"].replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                committed_at = None
        db.add(Commit(
            repository_id=repo.id, sha=c["sha"], author=c["author"], message=c["message"][:2000],
            additions=c["additions"], deletions=c["deletions"], changed_files=c["changed_files"],
            files=c.get("files", []), committed_at=committed_at,
        ))
        new_commits += 1
    db.commit()
    publish(db, "commit.created", project_id=repo.project_id, source="repository-service",
            payload={"repository_id": repo.id, "new_commits": new_commits})

    # Real code analysis
    module_metrics = aggregate_module_metrics(files, commits)
    saved_modules = []
    for m in module_metrics:
        existing = db.query(CodeModule).filter(
            CodeModule.repository_id == repo.id, CodeModule.path == m["path"]
        ).first()
        historical_defects = existing.historical_defects if existing else 0
        test_coverage = existing.test_coverage if existing else 0.5  # unknown until a real coverage report is ingested
        if existing:
            existing.loc = m["loc"]
            existing.cyclomatic_complexity = m["cyclomatic_complexity"]
            existing.num_functions = m["num_functions"]
            existing.churn_30d = m["churn_30d"]
            existing.num_commits = m["num_commits"]
            existing.num_authors = m["num_authors"]
            existing.last_analyzed_at = dt.datetime.utcnow()
            db.add(existing)
            saved_modules.append(existing)
        else:
            mod = CodeModule(
                repository_id=repo.id, path=m["path"], loc=m["loc"],
                cyclomatic_complexity=m["cyclomatic_complexity"], num_functions=m["num_functions"],
                churn_30d=m["churn_30d"], num_commits=m["num_commits"], num_authors=m["num_authors"],
                historical_defects=historical_defects, test_coverage=test_coverage,
            )
            db.add(mod)
            saved_modules.append(mod)
    repo.last_synced_at = dt.datetime.utcnow()
    db.add(repo)
    db.commit()
    for mod in saved_modules:
        db.refresh(mod)
    publish(db, "code.analysis.completed", project_id=repo.project_id, source="code-analysis-service",
            payload={"repository_id": repo.id, "modules_analyzed": len(saved_modules)})

    # Real ML prediction + real SHAP explanation + rule-based recommendations
    predictions_created = []
    for mod in saved_modules:
        features = {
            "loc": mod.loc, "cyclomatic_complexity": mod.cyclomatic_complexity,
            "num_functions": mod.num_functions, "churn_30d": mod.churn_30d,
            "num_commits": mod.num_commits, "num_authors": mod.num_authors,
            "historical_defects": mod.historical_defects, "test_coverage": mod.test_coverage,
        }
        result = predict_defect_probability(features)

        pred = Prediction(
            project_id=repo.project_id, target_type="module_defect_risk", target_ref=mod.path,
            probability=result["probability"], confidence=result["confidence"],
            model_name=result["model_name"], model_version=result["model_version"],
            dataset_version=result["dataset_version"], feature_schema_version=result["feature_schema_version"],
            input_features=features,
        )
        db.add(pred)
        db.commit()
        db.refresh(pred)
        publish(db, "model.prediction.created", project_id=repo.project_id, source="ai-ml-service",
                payload={"module": mod.path, "probability": result["probability"]})

        expl = Explanation(
            prediction_id=pred.id, shap_values=result["shap_values"],
            top_positive_factors=result["top_positive_factors"],
            top_negative_factors=result["top_negative_factors"],
            explanation_text=result["explanation_text"],
        )
        db.add(expl)
        db.commit()
        db.refresh(expl)

        recs = evaluate_module_recommendations(db, repo.project_id, mod, pred, expl)
        for r in recs:
            publish(db, "recommendation.created", project_id=repo.project_id, source="recommendation-service",
                    payload={"recommendation_id": r.id, "action": r.recommended_action, "module": mod.path})

        predictions_created.append({
            "module": mod.path, "probability": result["probability"],
            "explanation_text": result["explanation_text"], "recommendations_created": len(recs),
        })

    return {
        "repository_id": repo.id,
        "new_commits": new_commits,
        "modules_analyzed": len(saved_modules),
        "predictions": predictions_created,
    }


@router.post("/{repository_id}/sync")
def sync_repository(repository_id: str, max_files: int = 20,
                     user: User = Depends(get_current_user), db: Session = Depends(get_db),
                     github_token: str = Header(default=None, alias="X-GitHub-Token")):
    repo = db.query(Repository).filter(Repository.id == repository_id).first()
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    return _sync_repository_impl(repo, db, max_files=max_files, github_token=github_token)
