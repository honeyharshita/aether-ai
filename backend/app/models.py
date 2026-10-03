"""
AetherAI domain models.

Each logical group below maps to a service boundary from the spec
(Auth, Project, Repository, Code Analysis, Issue, Task, AI/ML,
Explainability, Recommendation, Monitoring/Events, Audit). They currently
share one physical Postgres database for local-demo simplicity, but each
group owns its own tables and is never queried across boundaries except
through the router/service layer -- so splitting into physically separate
databases later (per spec Section 8) is a deployment change, not a
redesign.
"""
import uuid
import datetime as dt
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, ForeignKey, Text, JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


def now():
    return dt.datetime.utcnow()


# ---------------------------------------------------------------- AUTH ----

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=gen_uuid)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    role = Column(String, nullable=False, default="VIEWER")
    # ADMIN, PROJECT_MANAGER, DEVELOPER, QA_ENGINEER, SECURITY_ENGINEER,
    # DATA_SCIENTIST, VIEWER
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String, unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)


# ------------------------------------------------------------- PROJECT ----

class Project(Base):
    __tablename__ = "projects"
    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    is_demo = Column(Boolean, default=False)  # spec Section 47/77.11 labeling
    owner_id = Column(String, ForeignKey("users.id"))
    created_at = Column(DateTime, default=now)

    repositories = relationship("Repository", back_populates="project")
    issues = relationship("Issue", back_populates="project")
    tasks = relationship("Task", back_populates="project")


# ---------------------------------------------------------- REPOSITORY ----

class Repository(Base):
    __tablename__ = "repositories"
    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"))
    provider = Column(String, default="github")  # github/gitlab/local/csv
    full_name = Column(String)  # e.g. "octocat/Hello-World"
    default_branch = Column(String, default="main")
    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)

    project = relationship("Project", back_populates="repositories")
    commits = relationship("Commit", back_populates="repository")
    modules = relationship("CodeModule", back_populates="repository")


class Commit(Base):
    __tablename__ = "commits"
    id = Column(String, primary_key=True, default=gen_uuid)
    repository_id = Column(String, ForeignKey("repositories.id"))
    sha = Column(String, index=True)
    author = Column(String)
    message = Column(Text)
    additions = Column(Integer, default=0)
    deletions = Column(Integer, default=0)
    changed_files = Column(Integer, default=0)
    files = Column(JSON, default=list)
    committed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)

    repository = relationship("Repository", back_populates="commits")


class CodeModule(Base):
    """A file/module analyzed by the Code Analysis service."""
    __tablename__ = "code_modules"
    id = Column(String, primary_key=True, default=gen_uuid)
    repository_id = Column(String, ForeignKey("repositories.id"))
    path = Column(String, index=True)
    loc = Column(Integer, default=0)
    cyclomatic_complexity = Column(Float, default=0.0)
    num_functions = Column(Integer, default=0)
    churn_30d = Column(Integer, default=0)  # lines changed in last 30 "commits window"
    num_commits = Column(Integer, default=0)
    num_authors = Column(Integer, default=0)
    historical_defects = Column(Integer, default=0)
    test_coverage = Column(Float, default=0.0)  # 0-1, from seed/demo data if unavailable
    last_analyzed_at = Column(DateTime, default=now)

    repository = relationship("Repository", back_populates="modules")


# --------------------------------------------------------------- ISSUE ----

class Issue(Base):
    __tablename__ = "issues"
    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"))
    external_id = Column(String, nullable=True)  # GitHub/Jira id if synced
    source = Column(String, default="internal")  # github/jira/internal
    title = Column(String)
    issue_type = Column(String, default="bug")  # bug/story/task-linked
    severity = Column(String, default="medium")  # low/medium/high/critical
    status = Column(String, default="open")
    related_module_path = Column(String, nullable=True)
    reopen_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=now)
    resolved_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="issues")


# ---------------------------------------------------------------- TASK ----

class Task(Base):
    __tablename__ = "tasks"
    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"))
    title = Column(String)
    status = Column(String, default="TODO")  # BACKLOG/TODO/IN_PROGRESS/IN_REVIEW/TESTING/DONE
    story_points = Column(Float, default=0.0)
    assignee_id = Column(String, ForeignKey("users.id"), nullable=True)
    deadline = Column(DateTime, nullable=True)
    dependency_count = Column(Integer, default=0)
    blocking_count = Column(Integer, default=0)
    effort_hours = Column(Float, default=0.0)
    created_at = Column(DateTime, default=now)
    completed_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="tasks")


# --------------------------------------------------------- TEST/CI DATA ----

class TestRun(Base):
    __tablename__ = "test_runs"
    id = Column(String, primary_key=True, default=gen_uuid)
    repository_id = Column(String, ForeignKey("repositories.id"))
    total_tests = Column(Integer, default=0)
    failed_tests = Column(Integer, default=0)
    coverage = Column(Float, default=0.0)
    build_status = Column(String, default="success")
    ran_at = Column(DateTime, default=now)


# ------------------------------------------------------------ SECURITY ----

class SecurityFinding(Base):
    __tablename__ = "security_findings"
    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"))
    source = Column(String)  # sonarqube/zap/trivy/dependency-check
    severity = Column(String)  # low/medium/high/critical
    cve = Column(String, nullable=True)
    component = Column(String, nullable=True)
    status = Column(String, default="open")
    detected_at = Column(DateTime, default=now)


# --------------------------------------------------------- AI / ML CORE ----

class ModelRegistryEntry(Base):
    """Model governance record (spec Section 25/50)."""
    __tablename__ = "model_registry"
    id = Column(String, primary_key=True, default=gen_uuid)
    model_name = Column(String)  # e.g. "defect_prediction_random_forest"
    model_version = Column(String)
    dataset_version = Column(String)
    feature_schema_version = Column(String)
    hyperparameters = Column(JSON, default=dict)
    metrics = Column(JSON, default=dict)  # accuracy/precision/recall/f1/roc_auc etc
    lifecycle_stage = Column(String, default="candidate")
    # candidate -> validated -> approved -> champion -> retired
    artifact_path = Column(String, nullable=True)
    trained_at = Column(DateTime, default=now)


class Prediction(Base):
    __tablename__ = "predictions"
    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"))
    target_type = Column(String)  # module_defect_risk / project_risk / task_priority
    target_ref = Column(String)  # module path / project_id / task_id
    probability = Column(Float, nullable=True)
    score = Column(Float, nullable=True)  # for non-probabilistic scores (e.g. priority 0-100)
    confidence = Column(Float, nullable=True)
    model_name = Column(String)
    model_version = Column(String)
    dataset_version = Column(String)
    feature_schema_version = Column(String)
    input_features = Column(JSON, default=dict)
    predicted_at = Column(DateTime, default=now)


class Explanation(Base):
    __tablename__ = "explanations"
    id = Column(String, primary_key=True, default=gen_uuid)
    prediction_id = Column(String, ForeignKey("predictions.id"))
    shap_values = Column(JSON, default=dict)
    top_positive_factors = Column(JSON, default=list)
    top_negative_factors = Column(JSON, default=list)
    explanation_text = Column(Text, default="")
    created_at = Column(DateTime, default=now)


class Recommendation(Base):
    __tablename__ = "recommendations"
    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"))
    source_prediction_id = Column(String, ForeignKey("predictions.id"), nullable=True)
    recommended_action = Column(String)
    reason = Column(Text)
    evidence = Column(JSON, default=dict)
    priority = Column(String, default="medium")
    responsible_role = Column(String, default="DEVELOPER")
    related_ref = Column(String, nullable=True)  # module path / task id / issue id
    status = Column(String, default="pending")  # pending/accepted/rejected/completed
    assigned_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    action_started_at = Column(DateTime, nullable=True)
    action_completed_at = Column(DateTime, nullable=True)
    risk_before = Column(Float, nullable=True)
    risk_after = Column(Float, nullable=True)
    outcome = Column(String, nullable=True)
    effectiveness_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=now)


# --------------------------------------------------- DIGITAL TWIN ----

class TwinNode(Base):
    __tablename__ = "twin_nodes"
    __table_args__ = (UniqueConstraint("project_id", "node_type", "ref_id"),)

    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    node_type = Column(String, nullable=False)
    ref_id = Column(String, nullable=False)
    attributes = Column(JSON, default=dict)
    updated_at = Column(DateTime, default=now, onupdate=now)


class TwinEdge(Base):
    __tablename__ = "twin_edges"
    __table_args__ = (UniqueConstraint("project_id", "source_id", "target_id", "edge_type"),)

    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    source_id = Column(String, ForeignKey("twin_nodes.id"), nullable=False)
    target_id = Column(String, ForeignKey("twin_nodes.id"), nullable=False)
    edge_type = Column(String, nullable=False)
    weight = Column(Float, default=1.0)
    evidence = Column(JSON, default=dict)


class TwinSnapshot(Base):
    __tablename__ = "twin_snapshots"

    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    snapshot_at = Column(DateTime, nullable=False, index=True)
    graph = Column(JSON, nullable=False)
    source = Column(String, default="materialized")
    created_at = Column(DateTime, default=now)


class FusionWeightHistory(Base):
    __tablename__ = "fusion_weight_history"

    id = Column(String, primary_key=True, default=gen_uuid)
    version = Column(Integer, nullable=False, unique=True)
    weights = Column(JSON, nullable=False)
    metrics = Column(JSON, default=dict)
    sample_size = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=now)


# --------------------------------------------------------- EVENTS / OPS ----

class EventLog(Base):
    """
    Persisted, replayable event log. Backs the local EventBus (see
    app/eventbus.py) which is a Postgres LISTEN/NOTIFY + table-backed
    stand-in for Kafka in this local demo -- same topic names and payload
    shape (event_id, event_type, project_id, source, timestamp,
    correlation_id, payload, schema_version) as spec Section 5, so a real
    Kafka producer/consumer can be substituted without changing callers.
    """
    __tablename__ = "event_log"
    event_id = Column(String, primary_key=True, default=gen_uuid)
    event_type = Column(String, index=True)
    project_id = Column(String, nullable=True)
    source = Column(String)
    correlation_id = Column(String, nullable=True)
    payload = Column(JSON, default=dict)
    schema_version = Column(String, default="1.0")
    status = Column(String, default="processed")  # processed / dead_letter
    created_at = Column(DateTime, default=now, index=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=True)
    action = Column(String)
    detail = Column(JSON, default=dict)
    created_at = Column(DateTime, default=now)
