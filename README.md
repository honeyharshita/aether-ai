# AetherAI — Adaptive Software Engineering Intelligence Platform

AetherAI observes a software project, predicts what's likely to go wrong
(defects, project risk, task priority), explains why using real SHAP
values, recommends concrete actions grounded in that evidence, and tracks
whether those actions actually reduced risk.

**Read [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) first.** This build is
an honest, working core of the full 77-section specification — not a
mockup, but also not the complete Spring Boot / Kafka / Kubernetes / MLflow
platform the spec describes at full scope. Every number this system shows
you is computed live from real data; nothing is hardcoded or randomly
generated. See `docs/ARCHITECTURE.md` for exactly what was simplified and
why, and how to extract it into the full target architecture.

## What's real here

- **Real trained ML models.** `backend/app/ml/train.py` trains Logistic
  Regression, Random Forest, and an MLP on a documented, reproducible
  seeded dataset, with a genuine train/val/test split. Every accuracy/
  precision/recall/F1/ROC-AUC number in the system comes from that run.
- **Real SHAP explainability.** `backend/app/ml/explain.py` computes
  actual SHAP values against the trained model for every prediction.
- **Real code analysis.** `backend/app/code_metrics.py` computes actual
  cyclomatic complexity (AST-based via `radon` for Python) and LOC from
  real file content, and real churn/commit/author counts from real commit
  history.
- **Real GitHub ingestion.** `backend/app/github_ingest.py` calls the live
  GitHub REST API for public repositories and, after per-user GitHub OAuth,
  private repositories the user can access.
- **Real, evidence-based recommendations.** `backend/app/recommend.py`
  fires rules only against real prediction + evidence rows already in the
  database, and tracks real before/after risk to compute effectiveness.
- **Real event log.** Every state change publishes a Kafka-shaped event
  (same topic names/payload shape the spec asks for) into a persisted,
  queryable event log.
- **Engineering digital twin.** Materialized nodes, edges, and snapshots
  are built from stored modules, commits, predictions, issues, and tasks;
  projects with no analyzed repository return an empty graph.
- **Counterfactual risk simulation.** Module feature overrides are scored
  by the trained champion model with SHAP explanations and are explicitly
  labeled hypothetical; simulation requests do not create predictions.
- **Evidence-based blast radius.** Risk propagates over repeated real
  co-change edges, with commit counts and paths returned as evidence.
- **Adaptive recommendation ranking.** Versioned ranking weights are
  recalibrated from measured completed outcomes, with a five-outcome floor.
- **Engineering memory.** A deterministic timeline merges persisted
  project events, prediction deltas, and recommendation outcomes; it can
  be filtered and exported as Markdown.

## Quick start (Docker Compose)

```bash
cp .env.example .env      # fill in JWT_SECRET at minimum
docker compose up --build
```

- Backend API + docs: http://localhost:8000/docs
- Dashboard: http://localhost:8080
- Postgres: localhost:5432

On first boot the backend image trains the real ML pipeline
(`app.ml.train`) so predictions work immediately.

## Quick start (without Docker)

```bash
# 1. Postgres
sudo apt-get install postgresql
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'aetherpass';"
sudo -u postgres createdb aetherai

# 2. Backend
cd backend
pip install -r requirements.txt
export DATABASE_URL=postgresql+psycopg2://postgres:aetherpass@localhost:5432/aetherai
python -m app.ml.train              # trains the real defect-prediction model
uvicorn app.main:app --reload --port 8000

# 3. Frontend (any static server)
cd ../frontend
python dev_server.py
```

Open http://localhost:8080, register an account, then either click **Seed
Demo Project** to see the full predict → explain → recommend pipeline run
against a reproducible, clearly-labeled demo dataset, or go to **Connect
Repo** and paste any GitHub link (full URL or `owner/repo`) to analyze a
real repository end to end.

**If login says "failed to fetch":** the dashboard can't reach the
backend. Check the API address shown under the login form — it defaults
to the same host the page was loaded from, port 8000. If your backend is
somewhere else, edit that field and click "save". The dot next to it is
green when connected, red when not. Most commonly this means the backend
just isn't running yet — start it first (see above), then reload.

**GitHub rate limits:** without a `GITHUB_TOKEN` set in `.env`, GitHub
allows 60 unauthenticated API requests/hour per IP, shared across
everyone on that network. If "Connect Repo" fails with a rate-limit
error, generate a token at https://github.com/settings/tokens (no scopes
needed for public repos) and set `GITHUB_TOKEN=...` in `.env`, then
restart the backend.

## Running the tests

```bash
cd backend
createdb aetherai_test   # once
pytest tests/ -v
```

The suite covers ML pipeline validity, code metrics, authentication,
repository flows, graph and snapshot evidence, simulations, blast radius,
recommendation calibration, memory timelines, and full API integration.

## Project layout

```
aetherai/
  backend/            FastAPI application (modular monolith; see ARCHITECTURE.md)
    app/
      routers/         auth, projects, repositories, issues, tasks,
            predictions, recommendations, security, monitoring,
            demo, twin, memory
      ml/               dataset.py, train.py, explain.py — real ML pipeline
      models.py         SQLAlchemy schema (one table group per service boundary)
      eventbus.py       Kafka-shaped event bus (Postgres-backed for local demo)
      code_metrics.py   real complexity/LOC/churn computation
      github_ingest.py  real GitHub REST API client
      recommend.py      rule-based recommendation engine + task priority scoring
    soap_adapter/       isolated SOAP 1.1 demo service (REST vs SOAP comparison)
    tests/
  frontend/           static dashboard (no build step)
  infrastructure/
    docker/, terraform/, kubernetes/, vagrant/
  docs/
    ARCHITECTURE.md   full architecture + deviations from spec
    LIMITATIONS.md     what's simplified, and why
    NOVELTY.md          research contribution framing (spec Section 65)
  .github/workflows/ci.yml
```

## API documentation

Interactive OpenAPI docs are auto-generated by FastAPI at `/docs` once the
backend is running.
