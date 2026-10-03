import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ml import anomaly as anomaly_module


def test_autoencoder_trains_and_produces_valid_manifest(tmp_path):
    anomaly_module.ARTIFACT_DIR = str(tmp_path)
    manifest = anomaly_module.train_autoencoder(n_samples=600, seed=1)
    assert manifest["n_normal_training_rows"] > 0
    assert manifest["anomaly_threshold"] > 0
    assert os.path.exists(os.path.join(str(tmp_path), "autoencoder.joblib"))


def test_extreme_module_scores_higher_than_normal_module(tmp_path):
    anomaly_module.ARTIFACT_DIR = str(tmp_path)
    anomaly_module.train_autoencoder(n_samples=800, seed=1)

    normal_like = {"loc": 200, "cyclomatic_complexity": 3, "num_functions": 10,
                    "churn_30d": 2, "num_commits": 5, "num_authors": 2,
                    "historical_defects": 0, "test_coverage": 0.85}
    extreme = {"loc": 2000, "cyclomatic_complexity": 40, "num_functions": 80,
               "churn_30d": 150, "num_commits": 60, "num_authors": 15,
               "historical_defects": 20, "test_coverage": 0.05}

    normal_result = anomaly_module.score_anomaly(normal_like)
    extreme_result = anomaly_module.score_anomaly(extreme)

    assert extreme_result["reconstruction_error"] > normal_result["reconstruction_error"]
    assert extreme_result["is_anomaly"] is True
    assert normal_result["is_anomaly"] is False
