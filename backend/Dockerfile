FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app
ENV MODEL_ARTIFACT_DIR=/app/model_artifacts

# Train the champion model once at image build time so the container has
# real artifacts on first boot (spec Section 47: never fake predictions;
# if no model exists yet, /api/v1/models/training-manifest returns 404
# with a clear message instead of a fabricated result).
RUN python -m app.ml.train && python -m app.ml.anomaly

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -f http://localhost:8000/api/v1/monitoring/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
