# Research Novelty Statement

Per the specification's own instruction (Section 65), novelty is claimed
carefully here. None of the individual techniques used — logistic
regression, random forests, SHAP, event-driven architecture, rule-based
recommendation systems — are novel on their own, and this document does
not claim otherwise.

## What this system actually contributes

**A continuous, closed-loop architecture connecting software engineering
signals to explained predictions to tracked-outcome recommendations.**
Concretely, and verifiably in this codebase:

1. **Multi-source feature fusion**: a single prediction (`app/ml/explain.py`)
   is computed from code metrics (complexity, LOC), temporal/commit signals
   (churn, author count), and historical defect counts, joined per module
   from three independently-owned data sources (`code_modules`, `commits`,
   `issues`).

2. **Evidence-gated recommendations**: `app/recommend.py`'s rule engine
   only fires against real prediction + evidence rows already persisted —
   a recommendation's `evidence` JSON field is a snapshot of exactly the
   data that triggered it, making every recommendation auditable back to
   its cause.

3. **Outcome feedback loop**: `app/routers/recommendations.py`'s lifecycle
   (`pending → accepted → completed`) captures `risk_before` from the
   prediction that triggered the recommendation and `risk_after` from the
   next prediction on the same target, computing a real
   `effectiveness_score` — the mechanism the spec's AAMRF concept
   describes for learning which recommendations actually help, even
   though this build's fusion weights are currently fixed rather than
   recalibrated from that feedback (see `docs/LIMITATIONS.md`).

4. **Full prediction governance**: every `Prediction` row stores its
   model name/version, dataset version, and feature schema version
   alongside the raw input features — reproducibility and auditability
   without a separate tracking server.

5. **Event-sourced architecture with real-time inference**: the full
   observe → analyze → predict → explain → recommend chain fires as a
   real event cascade (`app/eventbus.py`), not a batch job — demonstrated
   end-to-end in `app/routers/repositories.py:sync_repository`.

## What is not claimed

- Random Forest / Logistic Regression / MLP are standard, well-established
  classifiers, not novel algorithms.
- SHAP is Lundberg & Lee's established method, used here as-is.
- The event-driven pattern (publish/subscribe with a durable log) is a
  standard architecture pattern, implemented here on Postgres rather than
  Kafka (see `docs/LIMITATIONS.md`).
- The adaptive weight *recalibration* the spec's AAMRF concept describes
  (learning fusion weights from validation outcomes over time) is
  represented in the schema and outcome-tracking mechanism but is not yet
  an implemented learning loop in this build — the `effectiveness_score`
  is computed and stored, but nothing currently re-trains fusion weights
  from it. That is the most direct next step toward the full AAMRF
  concept described in the original specification.
