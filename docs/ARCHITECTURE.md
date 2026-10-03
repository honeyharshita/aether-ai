# Architecture

## System overview

```
                         ┌─────────────────────┐
                         │   Dashboard (SPA)    │
                         │  frontend/index.html │
                         └──────────┬───────────┘
                                    │ HTTPS/JSON (JWT bearer)
                         ┌──────────▼───────────┐
                         │   FastAPI backend     │
                         │  (modular monolith)   │
                         │                       │
   GitHub REST API ◄─────┤ github_ingest.py      │
                         │ code_metrics.py        │
                         │ ml/{dataset,train,     │
                         │     explain}.py        │
                         │ recommend.py           │
                         │ eventbus.py ───────────┼──► event_log table
                         │ routers/*               │    (Kafka-shaped)
                         └──────────┬───────────┘
                                    │ SQLAlchemy
                         ┌──────────▼───────────┐
                         │   PostgreSQL 16       │
                         └───────────────────────┘

   (isolated, not on the request path above)
   soap_adapter/service.py  — SOAP 1.1 demo wrapping the same health logic
```

## Service boundaries (mirrors the spec's microservice list)

Each module below owns its own table group and is only ever touched by
its own router — nothing reaches across boundaries by querying another
module's tables directly, matching the spec's "database-per-service"
principle in spirit even though they currently share one physical
Postgres instance (see "Deviations from spec" below for the real-service
extraction path).

| Spec service | This build | Tables owned |
|---|---|---|
| Auth Service | `app/routers/auth.py` | `users` |
| Project Service | `app/routers/projects.py` | `projects` |
| Repository Service | `app/routers/repositories.py`, `github_ingest.py` | `repositories`, `commits` |
| Code Analysis Service | `code_metrics.py` (called from repositories router) | `code_modules` |
| Bug/Issue Service | `app/routers/issues.py` | `issues` |
| Task Service | `app/routers/tasks.py` | `tasks` |
| Test/CI Service | (schema present: `TestRun`) | `test_runs` |
| Security Service | `app/routers/security.py` | `security_findings` |
| AI/ML Service | `app/ml/*`, `app/routers/predictions.py` | `predictions`, `model_registry` |
| Explainability Service | `app/ml/explain.py` | `explanations` |
| Recommendation Service | `app/recommend.py`, `app/routers/recommendations.py` | `recommendations` |
| Monitoring Service | `app/routers/monitoring.py` | `event_log`, `audit_log` |
| Digital Twin Service | `app/routers/twin.py`, `app/risk_propagation.py` | `twin_nodes`, `twin_edges`, `twin_snapshots` |
| Engineering Memory | `app/routers/memory.py` | read model over `event_log`, `predictions`, `recommendations`, `audit_log` |
| Fusion Calibration | `app/ml/recalibrate.py`, `app/routers/predictions.py` | `fusion_weight_history` |

## The real-time event flow (spec Section 46)

This is implemented exactly as specified, end-to-end, in
`app/routers/repositories.py:sync_repository`:

```
GitHub sync
  → commit.created (persisted event)
  → real code metrics computed (radon + real commit aggregation)
  → code.analysis.completed
  → real ML prediction (trained Random Forest/LogReg/MLP)
  → model.prediction.created
  → real SHAP explanation computed
  → rule engine evaluates real evidence
  → recommendation.created (only if a rule's real condition is met)

The persisted commit file lists also materialize real module co-change
edges for the Digital Twin and bounded blast-radius calculations. A daily
APScheduler job snapshots non-empty project graphs; graph reads refresh
the materialization immediately when the underlying graph changes.
```

The same flow (minus the live GitHub call) runs for the seeded demo
project in `app/routers/demo.py`, so the pipeline can be exercised without
network access or GitHub rate limits.

In a production deployment, `sync_repository` would be triggered from a
GitHub webhook (signature-validated) rather than called synchronously by
the user clicking "Connect" — the code is structured so that swap is a
routing change, not a rewrite, because every stage already publishes its
event to the bus rather than assuming synchronous completion.

## Deviations from spec, and the extraction path back to it

See `docs/LIMITATIONS.md` for the full, honest list of what's simplified
and why. In short: this is a Python/FastAPI modular monolith instead of
18 Spring Boot microservices behind Eureka + a gateway, and the event bus
is Postgres-backed instead of a real Kafka broker. Both were deliberate
choices to keep everything in this repository **real, running, and
tested**, rather than extensive but unverified.

To extract a module into a real separate service:
1. Copy its router file + the models it owns into a new service directory.
2. Give it its own database (or schema).
3. Replace direct Python calls to other modules (e.g. `recommend.py`
   calling into prediction data) with either an HTTP call to the owning
   service's API, or — preferably — an event subscription via the same
   `eventbus.py` topic names, so the service boundary becomes a real
   network boundary without changing the event contracts.
4. Point `eventbus.py` at a real Kafka broker (see the module's docstring
   for exactly what to swap).

## Data model

See `backend/app/models.py` for the full, commented SQLAlchemy schema.
Every prediction stores `model_name`, `model_version`, `dataset_version`,
`feature_schema_version`, and its raw `input_features` — full model
governance/auditability per spec Section 50, without needing a separate
MLflow server to see which model/data produced which prediction.

`POST /predictions/simulate` reuses the same champion predictor and SHAP
path without writing a `Prediction` row. Recommendation fusion weights
are versioned separately and recalibrated only from measured completed
outcomes; recalibration affects future recommendation ranking, not the
defect model.

## Frontend

`frontend/index.html` is a single-file, no-build-step dashboard (vanilla
HTML/CSS/JS) that talks to the backend purely over its public REST API —
it renders nothing that isn't real API response data. Design uses IBM
Plex Mono/Sans for a technical-observability register, with risk severity
communicated through a consistent color scale (teal → amber → red) used
throughout the Code Risk, Recommendations, and Security views.
