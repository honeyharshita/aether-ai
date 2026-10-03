"""
Explainability service logic (spec Section 21).

Computes real SHAP values against the trained champion model for a given
feature vector, and derives a plain-language explanation and a top
positive/negative factor list from those SHAP values -- nothing here is
templated text unconnected to the actual model output.
"""
import json
import os
import joblib
import numpy as np
import shap

from app.config import settings
from app.ml.dataset import FEATURE_COLUMNS

_explainer_cache = {}


def _load_manifest():
    with open(os.path.join(settings.MODEL_ARTIFACT_DIR, "training_manifest.json")) as f:
        return json.load(f)


def _load_champion():
    manifest = _load_manifest()
    model = joblib.load(os.path.join(settings.MODEL_ARTIFACT_DIR, "champion_model.joblib"))
    scaler = None
    if manifest["uses_scaler"]:
        scaler = joblib.load(os.path.join(settings.MODEL_ARTIFACT_DIR, "champion_scaler.joblib"))
    return model, scaler, manifest


def predict_defect_probability(features: dict) -> dict:
    """
    features: dict with keys matching FEATURE_COLUMNS (loc, cyclomatic_complexity,
    num_functions, churn_30d, num_commits, num_authors, historical_defects,
    test_coverage).
    Returns probability, model metadata, and a real SHAP-based explanation.
    """
    model, scaler, manifest = _load_champion()
    x = np.array([[features.get(col, 0.0) for col in FEATURE_COLUMNS]], dtype=float)
    x_model_input = scaler.transform(x) if scaler is not None else x

    proba = float(model.predict_proba(x_model_input)[0, 1])

    cache_key = manifest["champion_model"]
    if cache_key not in _explainer_cache:
        # Use a small real background sample for the explainer so it
        # reflects the actual training distribution rather than zeros.
        from app.ml.dataset import generate_dataset
        bg_df = generate_dataset(n_samples=100, seed=7)
        bg = bg_df[FEATURE_COLUMNS].values
        bg_input = scaler.transform(bg) if scaler is not None else bg
        if manifest["champion_model"] == "random_forest":
            explainer = shap.TreeExplainer(model)
        else:
            explainer = shap.KernelExplainer(model.predict_proba, bg_input[:50])
        _explainer_cache[cache_key] = explainer
    explainer = _explainer_cache[cache_key]

    if manifest["champion_model"] == "random_forest":
        raw = explainer.shap_values(x_model_input)
        # shap_values for binary RF classifier: list[class][sample][feature] or array
        sv = raw[1][0] if isinstance(raw, list) else raw[0]
    else:
        raw = explainer.shap_values(x_model_input, nsamples=100)
        sv = raw[1][0] if isinstance(raw, list) else raw[0]

    sv = np.array(sv).flatten()
    contributions = sorted(
        zip(FEATURE_COLUMNS, sv, x.flatten().tolist()),
        key=lambda t: t[1],
    )
    negative_factors = [
        {"feature": f, "shap_value": round(float(v), 4), "value": val}
        for f, v, val in contributions if v < 0
    ][:5]
    positive_factors = [
        {"feature": f, "shap_value": round(float(v), 4), "value": val}
        for f, v, val in reversed(contributions) if v > 0
    ][:5]

    explanation_text = _build_explanation_text(proba, positive_factors, negative_factors)

    return {
        "probability": round(proba, 4),
        "confidence": round(abs(proba - 0.5) * 2, 4),  # distance from decision boundary
        "model_name": manifest["champion_model"],
        "model_version": manifest["model_version"],
        "dataset_version": manifest["dataset_version"],
        "feature_schema_version": manifest["feature_schema_version"],
        "shap_values": {f: round(float(v), 4) for f, v, _ in contributions},
        "top_positive_factors": positive_factors,
        "top_negative_factors": negative_factors,
        "explanation_text": explanation_text,
    }


_FRIENDLY_NAMES = {
    "loc": "lines of code",
    "cyclomatic_complexity": "cyclomatic complexity",
    "num_functions": "number of functions",
    "churn_30d": "recent code churn",
    "num_commits": "commit frequency",
    "num_authors": "number of contributing authors",
    "historical_defects": "prior defect history",
    "test_coverage": "test coverage",
}


def _build_explanation_text(proba, positive_factors, negative_factors):
    pct = round(proba * 100, 1)
    parts = [f"Defect risk: {pct}%."]
    if positive_factors:
        names = ", ".join(_FRIENDLY_NAMES.get(f["feature"], f["feature"]) for f in positive_factors[:3])
        parts.append(f"Increased by: {names}.")
    if negative_factors:
        names = ", ".join(_FRIENDLY_NAMES.get(f["feature"], f["feature"]) for f in negative_factors[:3])
        parts.append(f"Reduced by: {names}.")
    return " ".join(parts)
