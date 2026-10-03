#!/usr/bin/env bash
# One-shot local (non-Docker) setup: Postgres + Python deps + trained model.
set -e

echo "== Installing PostgreSQL (if not already installed) =="
if ! command -v psql &> /dev/null; then
  sudo apt-get update && sudo apt-get install -y postgresql postgresql-contrib
fi
sudo service postgresql start

echo "== Creating database =="
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'aetherpass';" || true
sudo -u postgres createdb aetherai || echo "(aetherai db already exists)"
sudo -u postgres createdb aetherai_test || echo "(aetherai_test db already exists)"

echo "== Installing backend dependencies =="
cd "$(dirname "$0")/../backend"
pip install -r requirements.txt

echo "== Training the real defect-prediction model =="
export DATABASE_URL="postgresql+psycopg2://postgres:aetherpass@localhost:5432/aetherai"
export PYTHONPATH=.
python -m app.ml.train

echo "== Running tests =="
export TEST_DATABASE_URL="postgresql+psycopg2://postgres:aetherpass@localhost:5432/aetherai_test"
pytest tests/ -v

echo ""
echo "Setup complete. Start the backend with:"
echo "  cd backend && export DATABASE_URL=$DATABASE_URL PYTHONPATH=. && uvicorn app.main:app --reload --port 8000"
echo "Then serve frontend/index.html with any static file server, e.g.:"
echo "  cd frontend && python3 -m http.server 8080"
