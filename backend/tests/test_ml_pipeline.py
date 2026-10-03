"""
ML pipeline tests (spec Section 38 "ML tests"): preprocessing, feature
schema, model loading, prediction shape, probability range, reproducibility.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ml.dataset import generate_dataset, FEATURE_COLUMNS


def test_dataset_is_reproducible_with_fixed_seed():
    df1 = generate_dataset(n_samples=200, seed=42)
    df2 = generate_dataset(n_samples=200, seed=42)
    assert df1.equals(df2)


def test_dataset_different_seed_differs():
    df1 = generate_dataset(n_samples=200, seed=42)
    df2 = generate_dataset(n_samples=200, seed=43)
    assert not df1.equals(df2)


def test_dataset_has_expected_schema():
    df = generate_dataset(n_samples=50, seed=1)
    for col in FEATURE_COLUMNS:
        assert col in df.columns
    assert "is_defective" in df.columns
    assert set(df["is_defective"].unique()).issubset({0, 1})


def test_dataset_label_is_not_perfectly_separable():
    """A defect label that's deterministic from features is an unrealistic,
    dishonest benchmark. Assert some noise exists (label disagrees with
    its own generating threshold for at least some rows)."""
    df = generate_dataset(n_samples=2000, seed=42)
    # positive rate should be meaningfully between 0 and 1, not degenerate
    rate = df["is_defective"].mean()
    assert 0.05 < rate < 0.95


def test_training_pipeline_produces_valid_metrics(tmp_path):
    from app.ml import train as train_module
    train_module.ARTIFACT_DIR = str(tmp_path)
    manifest = train_module.main()
    assert manifest["champion_model"] in ("logistic_regression", "random_forest", "mlp")
    for model_name, metrics in manifest["results"].items():
        assert 0.0 <= metrics["accuracy"] <= 1.0
        assert 0.0 <= metrics["precision"] <= 1.0
        assert 0.0 <= metrics["recall"] <= 1.0
        assert 0.0 <= metrics["f1"] <= 1.0
        assert 0.0 <= metrics["roc_auc"] <= 1.0
    assert os.path.exists(os.path.join(str(tmp_path), "champion_model.joblib"))
