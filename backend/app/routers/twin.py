import datetime as dt
import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import SessionLocal, get_db
from app.models import (
    CodeModule, Commit, Issue, Prediction, Project, Repository, Task, TwinEdge,
    TwinNode, TwinSnapshot, User,
)
from app.risk_propagation import cochange_evidence

router = APIRouter(prefix="/api/v1/twin", tags=["digital-twin"])
SNAPSHOT_INTERVAL = dt.timedelta(hours=24)


def _stable_id(project_id, node_type, ref_id):
    value = f"aetherai:{project_id}:{node_type}:{ref_id}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, value))


def _iso(value):
    return value.isoformat() if value else None


def _build_graph(db, project_id, as_of=None):
    historical = as_of is not None
    repos = db.query(Repository).filter(Repository.project_id == project_id).all()
    repo_by_id = {repo.id: repo for repo in repos}
    repo_ids = list(repo_by_id)
    if not repo_ids:
        return {"project_id": project_id, "nodes": [], "edges": []}, []

    modules = db.query(CodeModule).filter(CodeModule.repository_id.in_(repo_ids)).all()
    predictions_query = db.query(Prediction).filter(
        Prediction.project_id == project_id,
        Prediction.target_type == "module_defect_risk",
    )
    if historical:
        predictions_query = predictions_query.filter(Prediction.predicted_at <= as_of)
    predictions = predictions_query.order_by(Prediction.predicted_at.desc()).all()
    latest_prediction = {}
    for prediction in predictions:
        latest_prediction.setdefault(prediction.target_ref, prediction)

    commits_query = db.query(Commit).filter(Commit.repository_id.in_(repo_ids))
    if historical:
        commits_query = commits_query.filter(Commit.committed_at.isnot(None), Commit.committed_at <= as_of)
    commits = commits_query.order_by(Commit.committed_at.desc()).all()

    nodes = []
    edges_by_key = {}
    module_node_by_db_id = {}
    module_by_repo_path = {}
    module_rows_by_path = {}

    for module in modules:
        prediction = latest_prediction.get(module.path)
        if historical and not prediction and (not module.last_analyzed_at or module.last_analyzed_at > as_of):
            continue
        repo = repo_by_id[module.repository_id]
        ref_id = f"{repo.full_name}:{module.path}"
        node_id = _stable_id(project_id, "module", ref_id)
        metrics = prediction.input_features if historical and prediction else {}
        attributes = {
            "path": module.path,
            "repository": repo.full_name,
            "repository_id": repo.id,
            "loc": metrics.get("loc", module.loc),
            "cyclomatic_complexity": metrics.get("cyclomatic_complexity", module.cyclomatic_complexity),
            "num_functions": metrics.get("num_functions", module.num_functions),
            "churn_30d": metrics.get("churn_30d", module.churn_30d),
            "num_commits": metrics.get("num_commits", module.num_commits),
            "num_authors": metrics.get("num_authors", module.num_authors),
            "test_coverage": metrics.get("test_coverage", module.test_coverage),
            "historical_defects": metrics.get("historical_defects", module.historical_defects),
            "risk": prediction.probability if prediction else None,
            "prediction_at": _iso(prediction.predicted_at) if prediction else None,
            "last_analyzed_at": _iso(module.last_analyzed_at),
        }
        nodes.append({"id": node_id, "node_type": "module", "ref_id": ref_id, "attributes": attributes})
        module_node_by_db_id[module.id] = node_id
        module_by_repo_path[(module.repository_id, module.path)] = [node_id]
        module_rows_by_path.setdefault(module.path, []).append(module)

    if not module_node_by_db_id:
        return {"project_id": project_id, "nodes": [], "edges": []}, commits

    contributor_commits = {}
    for commit in commits:
        author = (commit.author or "").strip()
        if not author:
            continue
        contributor_commits.setdefault(author, []).append(commit)

    contributor_ids = {}
    for author, author_commits in contributor_commits.items():
        contributor_id = _stable_id(project_id, "contributor", author)
        contributor_ids[author] = contributor_id
        nodes.append({
            "id": contributor_id,
            "node_type": "contributor",
            "ref_id": author,
            "attributes": {"name": author, "commit_count": len(author_commits)},
        })

    cochanges = cochange_evidence(commits, module_by_repo_path)
    for (left_id, right_id), evidence in cochanges.items():
        edges_by_key[(left_id, right_id, "co_change")] = {
            "source_id": left_id,
            "target_id": right_id,
            "edge_type": "co_change",
            "weight": evidence["count"],
            "evidence": {
                "commit_count": evidence["count"],
                "recent_joint_commits": evidence["recent_count"],
                "commit_ids": evidence["commit_ids"][:20],
            },
        }

    authorship = {}
    for commit in commits:
        contributor_id = contributor_ids.get((commit.author or "").strip())
        if not contributor_id:
            continue
        for path in set(commit.files or []):
            module_ids = module_by_repo_path.get((commit.repository_id, path), [])
            for module_id in module_ids:
                key = (module_id, contributor_id, "authorship")
                item = authorship.setdefault(key, {"count": 0, "commit_ids": []})
                item["count"] += 1
                item["commit_ids"].append(commit.id)
    for (module_id, contributor_id, edge_type), evidence in authorship.items():
        edges_by_key[(module_id, contributor_id, edge_type)] = {
            "source_id": module_id,
            "target_id": contributor_id,
            "edge_type": edge_type,
            "weight": evidence["count"],
            "evidence": {"commit_count": evidence["count"], "commit_ids": evidence["commit_ids"][:20]},
        }

    if not historical:
        project_issues = db.query(Issue).filter(Issue.project_id == project_id).all()
        for issue in project_issues:
            issue_ref = issue.external_id or issue.id
            issue_id = _stable_id(project_id, "issue", issue_ref)
            nodes.append({
                "id": issue_id,
                "node_type": "issue",
                "ref_id": issue_ref,
                "attributes": {
                    "title": issue.title,
                    "status": issue.status,
                    "severity": issue.severity,
                    "source": issue.source,
                    "related_module_path": issue.related_module_path,
                    "created_at": _iso(issue.created_at),
                },
            })
            if issue.related_module_path:
                for module in module_rows_by_path.get(issue.related_module_path, []):
                    source_id = module_node_by_db_id[module.id]
                    edges_by_key[(source_id, issue_id, "related_issue")] = {
                        "source_id": source_id,
                        "target_id": issue_id,
                        "edge_type": "related_issue",
                        "weight": 1,
                        "evidence": {"issue_id": issue.id, "related_module_path": module.path},
                    }

        for task in db.query(Task).filter(Task.project_id == project_id).all():
            nodes.append({
                "id": _stable_id(project_id, "task", task.id),
                "node_type": "task",
                "ref_id": task.id,
                "attributes": {
                    "title": task.title,
                    "status": task.status,
                    "story_points": task.story_points,
                    "blocking_count": task.blocking_count,
                    "deadline": _iso(task.deadline),
                    "created_at": _iso(task.created_at),
                },
            })

    edges = list(edges_by_key.values())
    nodes.sort(key=lambda node: (node["node_type"], node["ref_id"]))
    edges.sort(key=lambda edge: (edge["edge_type"], edge["source_id"], edge["target_id"]))
    return {"project_id": project_id, "nodes": nodes, "edges": edges}, commits


def _materialize(db, project_id, graph, source="materialized"):
    db.query(TwinEdge).filter(TwinEdge.project_id == project_id).delete(synchronize_session=False)
    db.query(TwinNode).filter(TwinNode.project_id == project_id).delete(synchronize_session=False)
    db.add_all([
        TwinNode(
            id=node["id"], project_id=project_id, node_type=node["node_type"],
            ref_id=node["ref_id"], attributes=node["attributes"],
        )
        for node in graph["nodes"]
    ])
    db.flush()
    db.add_all([
        TwinEdge(
            project_id=project_id, source_id=edge["source_id"], target_id=edge["target_id"],
            edge_type=edge["edge_type"], weight=edge["weight"], evidence=edge["evidence"],
        )
        for edge in graph["edges"]
    ])
    db.commit()

    if not graph["nodes"]:
        return None
    latest = db.query(TwinSnapshot).filter(
        TwinSnapshot.project_id == project_id
    ).order_by(TwinSnapshot.snapshot_at.desc()).first()
    graph_fingerprint = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
    previous_fingerprint = hashlib.sha256(json.dumps(latest.graph, sort_keys=True).encode()).hexdigest() if latest else None
    now = dt.datetime.utcnow()
    if not latest or graph_fingerprint != previous_fingerprint or now - latest.snapshot_at >= SNAPSHOT_INTERVAL:
        snapshot = TwinSnapshot(project_id=project_id, snapshot_at=now, graph=graph, source=source)
        db.add(snapshot)
        db.commit()
        return snapshot.snapshot_at
    return latest.snapshot_at


def snapshot_all_projects():
    db = SessionLocal()
    try:
        project_ids = [project.id for project in db.query(Project.id).all()]
        for project_id in project_ids:
            graph, _ = _build_graph(db, project_id)
            if graph["nodes"]:
                _materialize(db, project_id, graph, source="daily_schedule")
    finally:
        db.close()


@router.get("/graph")
def current_graph(project_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Project not found")
    graph, _ = _build_graph(db, project_id)
    snapshot_at = _materialize(db, project_id, graph)
    return {**graph, "snapshot_at": _iso(snapshot_at), "empty": not graph["nodes"]}


@router.get("/snapshot")
def historical_snapshot(project_id: str, at: dt.datetime,
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not db.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Project not found")
    as_of = at.replace(tzinfo=None)
    now = dt.datetime.utcnow()
    if as_of > now:
        raise HTTPException(status_code=400, detail="Snapshot time cannot be in the future")

    snapshot = db.query(TwinSnapshot).filter(
        TwinSnapshot.project_id == project_id,
        TwinSnapshot.snapshot_at <= as_of,
    ).order_by(TwinSnapshot.snapshot_at.desc()).first()
    if snapshot:
        return {**snapshot.graph, "snapshot_at": _iso(snapshot.snapshot_at), "source": snapshot.source}

    graph, commits = _build_graph(db, project_id, as_of=as_of)
    if not graph["nodes"] or not commits:
        raise HTTPException(status_code=404, detail="No real twin data exists at or before that time")
    snapshot = TwinSnapshot(
        project_id=project_id,
        snapshot_at=as_of,
        graph=graph,
        source="reconstructed_from_predictions_and_commits",
    )
    db.add(snapshot)
    db.commit()
    return {**graph, "snapshot_at": _iso(snapshot.snapshot_at), "source": snapshot.source}