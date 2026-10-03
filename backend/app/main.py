"""
AetherAI backend entrypoint.

This is a modular monolith organized into the same service boundaries as
the target microservices architecture (Auth, Project, Repository, Code
Analysis, Issue, Task, AI/ML, Explainability, Recommendation, Security,
Monitoring) -- each in its own router/module with its own tables, talking
to each other only through the EventBus or direct in-process calls that
mirror an API boundary. This is an explicit, documented simplification
from the spec's Spring Boot / Eureka / API Gateway design so the whole
system can be verified running in this environment; see
docs/ARCHITECTURE.md "Deviations from spec" for the extraction path to
real separate services.
"""
import datetime as dt

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine, ensure_commit_files_column
from app.routers import auth, projects, repositories, issues, tasks, predictions, recommendations, security, monitoring, demo, twin, memory

Base.metadata.create_all(bind=engine)
ensure_commit_files_column()

app = FastAPI(
    title="AetherAI — Adaptive Software Engineering Intelligence Platform",
    version="0.1.0",
    description="Real-time software engineering intelligence: defect prediction, "
                 "project risk, explainability, and closed-loop recommendations.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local dev; restrict via env-based allowlist in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(repositories.router)
app.include_router(issues.router)
app.include_router(tasks.router)
app.include_router(predictions.router)
app.include_router(recommendations.router)
app.include_router(security.router)
app.include_router(monitoring.router)
app.include_router(demo.router)
app.include_router(twin.router)
app.include_router(memory.router)

_twin_snapshot_scheduler = BackgroundScheduler(daemon=True)


@app.on_event("startup")
def start_twin_snapshot_scheduler():
    if not _twin_snapshot_scheduler.running:
        _twin_snapshot_scheduler.add_job(
            twin.snapshot_all_projects,
            trigger="interval",
            hours=24,
            next_run_time=dt.datetime.now() + dt.timedelta(hours=24),
            id="daily-twin-snapshots",
            replace_existing=True,
        )
        _twin_snapshot_scheduler.start()


@app.on_event("shutdown")
def stop_twin_snapshot_scheduler():
    if _twin_snapshot_scheduler.running:
        _twin_snapshot_scheduler.shutdown(wait=False)


@app.get("/")
def root():
    return {
        "product": "AetherAI",
        "status": "running",
        "docs": "/docs",
        "health": "/api/v1/monitoring/health",
    }
