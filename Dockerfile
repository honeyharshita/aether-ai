FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY frontend /app/frontend

WORKDIR /app/backend
ENV PYTHONPATH=/app/backend
ENV DATABASE_URL=sqlite:////data/aetherai.db
ENV MODEL_ARTIFACT_DIR=/app/backend/model_artifacts
ENV PORT=7860

RUN mkdir -p /data && python -m app.ml.train && python -m app.ml.anomaly

EXPOSE 7860
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]