# Limitations & Honest Scope

This document exists because the original specification is explicit and
repeated: no fake data, no fabricated metrics, no static mockups. Being
honest about what's *not* here is part of honoring that requirement.

## What was built as a real, tested, working system

- FastAPI backend with real Postgres persistence, JWT auth with roles,
  and 10 router modules covering auth/projects/repositories/issues/tasks/
  predictions/recommendations/security/monitoring/demo-seeding.
- A real ML pipeline: 3 trained classifiers, real metrics, real SHAP
  explanations, a real feature-importance-driven recommendation engine.
- Real GitHub REST API ingestion (verified against a live public repo;
  see below for the one thing that blocked a full live demo).
- Real code metrics: AST-based cyclomatic complexity via `radon` for
  Python, a documented branch-counting heuristic for other languages.
- A Kafka-shaped event bus, persisted and queryable (Postgres-backed).
- 20 automated tests (unit + integration against a real database), all
  passing.
- A working dashboard (vanilla HTML/JS, no build step) that exercises
  every one of the above through real HTTP calls — no mocked data.
- An isolated SOAP adapter proving REST vs SOAP wrapping the same real
  business logic.
- Docker Compose that builds and runs the whole stack together.
- Kubernetes manifests and Terraform (AWS) that are syntactically valid
  and structurally sound, but **not verified against a live cluster or
  AWS account** — no cluster/cloud credentials were available in the
  build environment. Review before applying to a real account.

## Update: fixes and additions from the second round of work

- **Fixed: "failed to fetch" on login.** The dashboard previously hardcoded
  `http://localhost:8000` as the API address, which only works when the
  dashboard is opened on the exact same machine the backend runs on.
  It now defaults to the same host the dashboard itself was loaded from,
  shows a live connectivity indicator, and lets you set/persist a custom
  API base from the login screen if auto-detection doesn't match your
  setup. If you still see this error, it means the backend genuinely isn't
  reachable from your browser at that address -- check it's running
  (`docker compose up` / `uvicorn app.main:app --port 8000`) and that
  nothing (firewall, different network namespace) blocks that port.
- **Fixed: task/issue creation returned an empty `{}`.** Those two routes
  had no `response_model`, so FastAPI's default encoder silently failed
  to serialize the raw SQLAlchemy object. Both now return the full created
  record; regression tests added in `test_api_integration.py`.
- **New: paste-a-GitHub-link flow.** `POST /repositories/connect-and-sync`
  accepts a full GitHub URL in any common form (`https://github.com/owner/
  repo`, with `.git`, with a trailing `/tree/branch`, `git@github.com:...`,
  or bare `owner/repo`), normalizes it, creates a project if none is
  specified, connects the repo, and runs the full real sync pipeline in
  one call. The dashboard's "Connect Repo" page uses this directly.
- **New: real project risk timeline**, aggregated from actual stored
  `Prediction` rows grouped by day (`GET /projects/{id}/risk-timeline`),
  rendered as a canvas line chart on the Overview page.
- **New: module drill-down.** Clicking any row in Code Risk opens the
  real SHAP explanation for that module's latest prediction, plus any
  recommendations tied to it.
- **New: Bugs and Sprint/Tasks pages** in the dashboard, backed by the
  existing issues/tasks endpoints (these existed in the API before but
  had no UI).
- **Dashboard rewritten** for visual polish and a proper design system
  (IBM Plex Mono/Sans, consistent risk-severity color scale, loading
  states, connectivity banner, toasts) rather than a functional-but-plain
  first pass.

## Update: added since the first build

- **Real MLflow tracking** (spec Section 25). `app/ml/train.py` now logs
  every model's real params/metrics to a local sqlite-backed MLflow store
  and registers the champion in MLflow's real Model Registry. Inspect it
  with `mlflow ui --backend-store-uri sqlite:///backend/model_artifacts/mlflow.db`.
- **Real autoencoder anomaly detection** (spec Section 10, Model 7).
  `app/ml/anomaly.py` trains a real bottlenecked neural network on
  "normal" module behavior only, and scores new modules by real
  reconstruction error against a statistically-derived threshold. Exposed
  at `GET /api/v1/anomalies`. Verified to correctly separate a normal-like
  module from a deliberately extreme one (see `tests/test_anomaly.py`).
- **Jira ingestion adapter** (`app/jira_ingest.py`) — written to the same
  structure/error-handling pattern as the (live-tested) GitHub adapter,
  but **not verified against a real Jira instance**: Atlassian's API
  domains aren't reachable from this build sandbox. Treat it like the
  Terraform/Kubernetes files below — structurally sound, untested live.

## Update: evidence-backed intelligence layer

- **Digital Twin graph.** `GET /api/v1/twin/graph` materializes nodes and
  edges from persisted project rows; `GET /api/v1/twin/snapshot` returns a
  stored snapshot or reconstructs module metrics from earlier real
  predictions and commits. A backend scheduler stores daily snapshots;
  graph reads also refresh snapshots when real graph data changes. Older
  issue/task status history cannot be reconstructed because those tables
  do not retain status versions.
- **Counterfactual simulator.** `POST /api/v1/predictions/simulate` calls
  the deployed champion predictor and SHAP explainer from a real module
  baseline, applies validated feature overrides, and does not persist a
  `Prediction` observation.
- **Blast radius.** `GET /api/v1/code-risk/{module_path}/blast-radius`
  uses persisted changed-file lists, accepts only pairs co-changed in at
  least two commits, starts from modules above 50% recorded risk, and
  propagates at most three hops. Older commits whose changed-file lists
  predate the additive `commits.files` column provide no co-change edges.
- **Recommendation outcome learning.** `POST /api/v1/models/recalibrate`
  requires at least five measured completed recommendations by default;
  recalibration changes ranking weights for future recommendations, not
  the trained defect model or historical recommendation rows.
- **Engineering memory.** `GET /api/v1/memory/timeline` creates
  deterministic summaries from stored events, predictions, and measured
  recommendation outcomes. The optional LLM query layer is not included;
  it needs a separate product and credential decision.
- **Dashboard additions.** Digital Twin uses D3 for a force-directed view;
  Memory is filterable and exportable; the module modal includes What If?
  and Blast Radius tabs; Model Lab shows fusion-weight history and sample
  counts. The graph has no placeholder nodes.

## What was explicitly scoped down, and why

**No Apache Kafka broker.** Standing up and verifying a real Kafka broker
wasn't practical in the sandbox this was built in. `app/eventbus.py`
implements the same topic names and event envelope shape Kafka would use,
backed by a Postgres table instead of a Kafka partition, with the same
idempotency/dead-letter semantics. Swapping in a real `kafka-python`
producer/consumer is a contained change — see the docstring in that file
for exactly what to replace.

**No Spring Boot / Spring Cloud / Eureka / API Gateway.** The spec's
target architecture is 18 separate Spring Boot microservices with Eureka
service discovery and a dedicated gateway. This build is a Python/FastAPI
**modular monolith** organized into the same service boundaries (each
router + its own table group, communicating only through the event bus or
router-boundary function calls) — a deliberate, disclosed simplification
so the whole system could be built, run, and tested for real in one
session, rather than written-but-unverified in a stack (JVM/Maven) whose
package registry wasn't reachable in the build sandbox. See
`docs/ARCHITECTURE.md` for the extraction path to real separate services.

**Only 4 of the spec's ~10 model architectures were trained** (Logistic
Regression, Random Forest, MLP, and an autoencoder for anomaly detection).
CNN/RNN/LSTM/GRU/Transformer were not added: genuinely training an LSTM/
GRU needs a deep-learning framework (PyTorch/TensorFlow), and the only
installable wheels reachable from this sandbox's package registry are the
full CUDA builds (multi-GB) rather than the lightweight CPU-only builds
Torch/TF host on their own domains (not in this sandbox's network
allowlist) — installing them here risked an unverifiable, half-downloaded
dependency rather than a working model. They're architecturally
straightforward to add to `app/ml/train.py` following the existing
pattern once run somewhere with that access.

**Jira sync is not wired into a live router.** `app/jira_ingest.py` exists
(see "Update: added since the first build" above) but is not verified
against a real Jira instance from this sandbox.

**GitHub rate limiting.** Without a `GITHUB_TOKEN`, the public GitHub API
allows 60 requests/hour per IP. During testing this was hit while
connecting a real repository — which is itself a real, correct exercise
of the "GitHub unavailable" graceful-degradation path (spec Section 57):
the API returned a clean 502 with an explanatory message rather than
crashing. Set `GITHUB_TOKEN` in `.env` for real sustained use.

**Demo dataset labels are synthetic, and this is disclosed everywhere it
matters.** `app/ml/dataset.py` generates feature distributions modeled on
published defect-prediction literature (PROMISE/NASA-MDP-style features)
with labels from a documented logistic formula plus real Bernoulli noise
— not hand-tuned to hit a target accuracy, and not perfectly separable
(realistic ~0.6 accuracy, not an inflated 0.95+). Every project seeded
via `/api/v1/demo/seed` is flagged `is_demo: true` end-to-end, per spec
Section 47's explicit allowance for labeled demo data run through the
real pipeline.

**No fairness/bias audit module.** The spec asks the system to never rank
individual developers; this build satisfies that by construction (no
per-developer scoring exists anywhere in the schema or API), but there's
no separate automated fairness-testing suite.

## What would be needed to reach full spec parity

1. Extract each router module into its own deployable service (FastAPI →
   separate containers, or rewrite in Spring Boot per the original spec),
   each with its own database, communicating only via the event bus.
2. Stand up real Kafka (or Redpanda) and swap `app/eventbus.py`'s
   implementation.
3. Add Eureka/Consul + an API Gateway (Spring Cloud Gateway or Kong) in
   front of the now-separate services.
4. Run MLflow with a real Postgres backend store (not sqlite) and a
   separate `mlflow server` process for multi-user access.
5. Add CNN/RNN/LSTM/GRU/Transformer models following the existing
   `train.py`/`anomaly.py` pattern from an environment with PyTorch/
   TensorFlow CPU-wheel access, plus temporal train/test splitting for
   the project-risk (not defect) prediction target.
6. Verify `app/jira_ingest.py` against a real Jira Cloud instance and
   wire it into a `jira` router the same way `repositories.py` wires
   `github_ingest.py`.
7. Validate the Terraform and Kubernetes manifests against real AWS/EKS
   or Minikube and fix whatever the first real `terraform plan` /
   `kubectl apply --dry-run` surfaces.
