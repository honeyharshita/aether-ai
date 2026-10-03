"""
EventBus: a Kafka-shaped event abstraction.

WHY THIS EXISTS
----------------
The spec calls for Apache Kafka with named topics (commit.created,
prediction.created, recommendation.created, etc.), idempotent processing,
and dead-letter handling. Running a real Kafka broker isn't practical in
this sandbox, so this module implements the SAME topic names, the SAME
event envelope shape, and the SAME idempotency/dead-letter semantics,
backed by a Postgres table (EventLog) instead of a Kafka topic partition.

Every event is persisted (so the Event Monitor page has real data, not
mocked data) and dispatched synchronously in-process to registered
handlers. To swap in real Kafka: replace `publish()`'s body with a
`kafka-python`/`confluent-kafka` producer.send(topic, payload) call, and
replace `subscribe()` with a consumer loop -- callers (routers, ML
pipeline, recommendation engine) do not change.
"""
import uuid
import datetime as dt
from collections import defaultdict
from sqlalchemy.orm import Session
from app.models import EventLog

TOPICS = [
    "project.created", "project.updated",
    "repository.connected", "repository.synced",
    "commit.created",
    "issue.created", "issue.updated", "issue.closed",
    "task.created", "task.updated", "task.completed",
    "test.executed", "test.failed",
    "build.started", "build.failed", "build.succeeded",
    "security.scan.completed",
    "code.analysis.completed",
    "model.prediction.created",
    "recommendation.created",
    "recommendation.completed",
    "model.drift.detected",
    "model.retraining.started",
    "model.retraining.completed",
]

_handlers = defaultdict(list)
_seen_idempotency_keys = set()


def subscribe(event_type: str, handler):
    """Register an in-process handler for a topic."""
    _handlers[event_type].append(handler)


def publish(db: Session, event_type: str, project_id: str = None, source: str = "system",
            payload: dict = None, correlation_id: str = None, idempotency_key: str = None):
    """
    Publish an event: persist it (EventLog table = durable log, mirrors a
    Kafka topic) then dispatch to subscribers. Duplicate idempotency_keys
    are skipped (idempotent processing, spec Section 5). Handler failures
    are caught and the event is marked dead_letter rather than crashing
    the publisher (spec Section 5 dead-letter handling).
    """
    if idempotency_key and idempotency_key in _seen_idempotency_keys:
        return None
    if idempotency_key:
        _seen_idempotency_keys.add(idempotency_key)

    event = EventLog(
        event_id=str(uuid.uuid4()),
        event_type=event_type,
        project_id=project_id,
        source=source,
        correlation_id=correlation_id or str(uuid.uuid4()),
        payload=payload or {},
        schema_version="1.0",
        status="processed",
        created_at=dt.datetime.utcnow(),
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    for handler in _handlers.get(event_type, []):
        try:
            handler(db, event)
        except Exception as exc:  # noqa: BLE001
            event.status = "dead_letter"
            event.payload = {**(event.payload or {}), "_error": str(exc)}
            db.add(event)
            db.commit()
    return event
