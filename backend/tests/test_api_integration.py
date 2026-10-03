"""
API integration tests (spec Section 38 "@SpringBootTest"-equivalent):
service + real database, exercised through the real HTTP routes.

Uses a dedicated test database (aetherai_test) so these tests never touch
demo/dev data. Run: pytest tests/test_api_integration.py
"""
import os
import sys
import datetime as dt
from urllib.parse import parse_qs, urlparse
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg2://postgres:aetherpass@localhost:5432/aetherai_test",
)

from fastapi.testclient import TestClient  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Project, Repository, CodeModule, Commit, Issue, Task, Prediction, Recommendation  # noqa: E402

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def _reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="module")
def auth_headers():
    client.post("/api/v1/auth/register", json={
        "email": "tester@aetherai.dev", "password": "TestPassword123",
        "full_name": "Tester", "role": "ADMIN",
    })
    resp = client.post("/api/v1/auth/login", json={
        "email": "tester@aetherai.dev", "password": "TestPassword123",
    })
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_register_rejects_duplicate_email(auth_headers):
    resp = client.post("/api/v1/auth/register", json={
        "email": "tester@aetherai.dev", "password": "TestPassword123",
        "full_name": "Dup", "role": "VIEWER",
    })
    assert resp.status_code == 400


def test_login_rejects_wrong_password():
    resp = client.post("/api/v1/auth/login", json={
        "email": "tester@aetherai.dev", "password": "wrong-password",
    })
    assert resp.status_code == 401


def test_password_reset_email_uses_single_use_token_and_changes_password(auth_headers, monkeypatch):
    sent_messages = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            assert host == "smtp.example.test"
            assert port == 587
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def starttls(self):
            return None
        def login(self, username, password):
            return None
        def send_message(self, message):
            sent_messages.append(message)

    monkeypatch.setattr("app.routers.auth.settings.SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr("app.routers.auth.settings.SMTP_PORT", 587)
    monkeypatch.setattr("app.routers.auth.settings.SMTP_FROM_EMAIL", "security@example.test")
    monkeypatch.setattr("app.routers.auth.settings.SMTP_USERNAME", "mailer")
    monkeypatch.setattr("app.routers.auth.settings.SMTP_PASSWORD", "mailer-secret")
    monkeypatch.setattr("app.routers.auth.settings.SMTP_STARTTLS", True)
    monkeypatch.setattr("app.routers.auth.smtplib.SMTP", FakeSMTP)

    response = client.post("/api/v1/auth/forgot-password", json={"email": "tester@aetherai.dev"})
    assert response.status_code == 200
    assert sent_messages
    email_body = sent_messages[0].get_content()
    reset_url = next(line for line in email_body.splitlines() if "#password-reset=" in line)
    reset_token = parse_qs(urlparse(reset_url).fragment)["password-reset"][0]
    assert reset_token not in response.text

    reset = client.post("/api/v1/auth/reset-password", json={
        "token": reset_token,
        "new_password": "NewPassword456!",
    })
    assert reset.status_code == 200
    assert client.post("/api/v1/auth/reset-password", json={
        "token": reset_token,
        "new_password": "AnotherPassword789!",
    }).status_code == 400
    assert client.post("/api/v1/auth/login", json={
        "email": "tester@aetherai.dev", "password": "NewPassword456!",
    }).status_code == 200


def test_password_reset_reports_missing_smtp_configuration(monkeypatch):
    monkeypatch.setattr("app.routers.auth.settings.SMTP_HOST", "")
    monkeypatch.setattr("app.routers.auth.settings.SMTP_FROM_EMAIL", "")
    response = client.post("/api/v1/auth/forgot-password", json={"email": "unknown@example.com"})
    assert response.status_code == 503
    assert "SMTP_HOST" in response.json()["detail"]


def test_google_login_creates_or_returns_user(monkeypatch):
    called = {}

    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
        def json(self):
            return self._payload

    def fake_get(url, params=None, timeout=None):
        called['url'] = url
        called['params'] = params
        return FakeResponse(200, {
            "aud": "233695971962-8isndc6o3p4aflhpfah1o4oabf9rjm22.apps.googleusercontent.com",
            "email": "google.user@example.com",
            "name": "Google User",
            "email_verified": "true",
        })

    monkeypatch.setattr("app.routers.auth.requests.get", fake_get)
    monkeypatch.setattr("app.routers.auth.settings.GOOGLE_CLIENT_ID", "233695971962-8isndc6o3p4aflhpfah1o4oabf9rjm22.apps.googleusercontent.com")

    resp = client.post("/api/v1/auth/google", json={"credential": "fake-google-id-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert called["url"] == "https://oauth2.googleapis.com/tokeninfo"


def test_github_oauth_callback_creates_session(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload
        def json(self):
            return self.payload
        def raise_for_status(self):
            return None

    monkeypatch.setattr("app.routers.auth.settings.GITHUB_CLIENT_ID", "github-client")
    monkeypatch.setattr("app.routers.auth.settings.GITHUB_CLIENT_SECRET", "github-secret")
    monkeypatch.setattr("app.routers.auth.settings.GITHUB_REDIRECT_URI", "http://localhost:8000/api/v1/auth/github/callback")
    monkeypatch.setattr("app.routers.auth.settings.FRONTEND_URL", "http://localhost:8080")
    monkeypatch.setattr("app.routers.auth.requests.post", lambda *args, **kwargs: FakeResponse({"access_token": "github-user-token"}))

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/user"):
            return FakeResponse({"id": 123, "login": "github-user", "name": "GitHub User"})
        return FakeResponse([{"email": "github.user@example.com", "primary": True, "verified": True}])

    monkeypatch.setattr("app.routers.auth.requests.get", fake_get)
    response = client.get(
        "/api/v1/auth/github/callback",
        params={"code": "oauth-code", "state": "valid-state"},
        cookies={"github_oauth_state": "valid-state"},
        follow_redirects=False,
    )

    assert response.status_code == 307
    fragment = parse_qs(urlparse(response.headers["location"]).fragment)
    assert fragment["github_access_token"] == ["github-user-token"]
    user_response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {fragment['access_token'][0]}"})
    assert user_response.status_code == 200
    assert user_response.json()["email"] == "github.user@example.com"


def test_github_login_requests_account_picker(monkeypatch):
    monkeypatch.setattr("app.routers.auth.settings.GITHUB_CLIENT_ID", "github-client")
    monkeypatch.setattr("app.routers.auth.settings.GITHUB_CLIENT_SECRET", "github-secret")
    monkeypatch.setattr("app.routers.auth.settings.GITHUB_REDIRECT_URI", "http://localhost:8000/api/v1/auth/github/callback")

    response = client.get("/api/v1/auth/github", follow_redirects=False)
    query = parse_qs(urlparse(response.headers["location"]).query)

    assert response.status_code == 307
    assert query["prompt"] == ["select_account"]
    assert query["scope"] == ["read:user user:email repo"]
    assert "github_oauth_state" in response.headers["set-cookie"]


def test_github_repository_list_uses_user_token(auth_headers, monkeypatch):
    called = {}

    class FakeResponse:
        status_code = 200
        def json(self):
            return [{
                "full_name": "private-org/private-repo",
                "private": True,
                "default_branch": "main",
                "description": "Private project",
                "html_url": "https://github.com/private-org/private-repo",
                "updated_at": "2026-10-01T00:00:00Z",
            }]

    def fake_get(url, headers=None, params=None, timeout=None):
        called.update(url=url, headers=headers, params=params)
        return FakeResponse()

    monkeypatch.setattr("app.routers.auth.requests.get", fake_get)
    response = client.get(
        "/api/v1/auth/github/repositories",
        headers={**auth_headers, "X-GitHub-Token": "user-github-token"},
    )

    assert response.status_code == 200
    assert response.json()[0]["full_name"] == "private-org/private-repo"
    assert response.json()[0]["private"] is True
    assert called["headers"]["Authorization"] == "Bearer user-github-token"
    assert called["params"]["visibility"] == "all"


def test_protected_route_requires_auth():
    resp = client.get("/api/v1/projects")
    assert resp.status_code == 401


def test_create_and_list_project(auth_headers):
    resp = client.post("/api/v1/projects", json={"name": "Test Project"}, headers=auth_headers)
    assert resp.status_code == 201
    project_id = resp.json()["id"]

    resp = client.get("/api/v1/projects", headers=auth_headers)
    assert resp.status_code == 200
    assert any(p["id"] == project_id for p in resp.json())


def test_project_health_reports_no_data_before_any_signals(auth_headers):
    resp = client.post("/api/v1/projects", json={"name": "Empty Project"}, headers=auth_headers)
    project_id = resp.json()["id"]
    resp = client.get(f"/api/v1/projects/{project_id}/health", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["health_score"] is None  # no fake defaulted score

def test_seed_demo_produces_real_predictions_and_recommendations(auth_headers):
    resp = client.post("/api/v1/demo/seed", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_demo"] is True
    assert body["modules_seeded"] == 12
    for p in body["predictions"]:
        assert 0.0 <= p["probability"] <= 1.0

    project_id = body["project_id"]

    resp = client.get(f"/api/v1/projects/{project_id}/health", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["health_score"] is not None

    resp = client.get(f"/api/v1/recommendations?project_id={project_id}", headers=auth_headers)
    assert resp.status_code == 200
    recs = resp.json()
    assert len(recs) > 0
    for r in recs:
        assert r["reason"]  # every recommendation has an evidence-based reason

    resp = client.get(f"/api/v1/monitoring/events?project_id={project_id}", headers=auth_headers)
    assert resp.status_code == 200
    event_types = {e["event_type"] for e in resp.json()}
    assert "model.prediction.created" in event_types
    assert "recommendation.created" in event_types


def test_create_issue_returns_full_object(auth_headers):
    """Regression test: issue/task creation once returned an empty {}
    because the routes had no response_model, so FastAPI's default
    encoder couldn't serialize the raw SQLAlchemy object."""
    proj = client.post("/api/v1/projects", json={"name": "Issue Test Project"}, headers=auth_headers).json()
    resp = client.post("/api/v1/issues", json={
        "project_id": proj["id"], "title": "Login button broken", "severity": "high",
    }, headers=auth_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"]
    assert body["title"] == "Login button broken"
    assert body["severity"] == "high"
    assert body["status"] == "open"


def test_create_task_returns_full_object_and_priority_is_computed(auth_headers):
    proj = client.post("/api/v1/projects", json={"name": "Task Test Project"}, headers=auth_headers).json()
    resp = client.post("/api/v1/tasks", json={
        "project_id": proj["id"], "title": "Refactor auth module", "blocking_count": 3,
    }, headers=auth_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"]
    assert body["title"] == "Refactor auth module"
    assert body["status"] == "TODO"

    resp = client.get(f"/api/v1/tasks/{body['id']}/priority", headers=auth_headers)
    assert resp.status_code == 200
    priority = resp.json()
    assert 0 <= priority["score"] <= 100
    assert "components" in priority


def test_risk_timeline_reflects_real_predictions(auth_headers):
    seed = client.post("/api/v1/demo/seed", headers=auth_headers).json()
    project_id = seed["project_id"]
    resp = client.get(f"/api/v1/projects/{project_id}/risk-timeline", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["series"]) >= 1
    assert 0.0 <= body["series"][0]["avg_risk"] <= 1.0
    assert body["series"][0]["predictions"] == 12  # matches demo module count


def test_github_url_variants_are_accepted_by_connect_and_sync(auth_headers):
    """The endpoint should get past URL parsing for any valid GitHub link
    shape and fail (if it fails) only on the live network call -- never
    on malformed-input handling."""
    for bad_input in ["not a github link", "https://gitlab.com/foo/bar"]:
        resp = client.post("/api/v1/repositories/connect-and-sync", json={
            "full_name": bad_input, "provider": "github",
        }, headers=auth_headers)
        assert resp.status_code == 400
        assert "Could not parse a GitHub repository" in resp.json()["detail"]


def test_recommendation_lifecycle_and_effectiveness(auth_headers):
    seed = client.post("/api/v1/demo/seed", headers=auth_headers).json()
    project_id = seed["project_id"]
    recs = client.get(f"/api/v1/recommendations?project_id={project_id}", headers=auth_headers).json()
    rec_id = recs[0]["id"]

    resp = client.patch(f"/api/v1/recommendations/{rec_id}", json={"status": "accepted"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    assert resp.json()["risk_before"] is not None

    resp = client.patch(f"/api/v1/recommendations/{rec_id}", json={"status": "completed"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert resp.json()["outcome"] is not None


def _create_twin_evidence(auth_headers, project_name):
    project = client.post("/api/v1/projects", json={"name": project_name}, headers=auth_headers).json()
    user_id = client.get("/api/v1/auth/me", headers=auth_headers).json()["id"]
    db = SessionLocal()
    repository = Repository(project_id=project["id"], provider="github", full_name="example/twin", default_branch="main")
    db.add(repository)
    db.flush()
    module_checkout = CodeModule(
        repository_id=repository.id, path="src/checkout.py", loc=180,
        cyclomatic_complexity=12, num_functions=8, churn_30d=24,
        num_commits=4, num_authors=1, historical_defects=1, test_coverage=0.4,
    )
    module_cart = CodeModule(
        repository_id=repository.id, path="src/cart.py", loc=90,
        cyclomatic_complexity=5, num_functions=4, churn_30d=8,
        num_commits=2, num_authors=1, historical_defects=0, test_coverage=0.8,
    )
    db.add_all([module_checkout, module_cart])
    db.flush()
    db.add_all([
        Commit(
            repository_id=repository.id, sha="a" * 40, author="Ava",
            message="Update checkout and cart", changed_files=2,
            files=["src/checkout.py", "src/cart.py"], committed_at=dt.datetime(2025, 1, 1),
        ),
        Commit(
            repository_id=repository.id, sha="b" * 40, author="Ava",
            message="Fix checkout and cart", changed_files=2,
            files=["src/checkout.py", "src/cart.py"], committed_at=dt.datetime(2025, 1, 2),
        ),
    ])
    db.add(Issue(
        project_id=project["id"], source="internal", title="Checkout regression",
        issue_type="bug", severity="high", status="open", related_module_path="src/checkout.py",
    ))
    db.add(Task(project_id=project["id"], title="Add checkout tests", status="TODO"))
    feature_sets = [
        {"loc": 170, "cyclomatic_complexity": 11, "num_functions": 7, "churn_30d": 12,
         "num_commits": 3, "num_authors": 1, "historical_defects": 1, "test_coverage": 0.4},
        {"loc": 180, "cyclomatic_complexity": 12, "num_functions": 8, "churn_30d": 36,
         "num_commits": 4, "num_authors": 1, "historical_defects": 1, "test_coverage": 0.4},
    ]
    for risk, predicted_at, features in zip(
        (0.62, 0.81), (dt.datetime(2025, 1, 4), dt.datetime(2025, 1, 5)), feature_sets,
    ):
        db.add(Prediction(
            project_id=project["id"], target_type="module_defect_risk", target_ref="src/checkout.py",
            probability=risk, confidence=0.7, model_name="random_forest", model_version="v1",
            dataset_version="test", feature_schema_version="v1", input_features=features,
            predicted_at=predicted_at,
        ))
    db.add(Prediction(
        project_id=project["id"], target_type="module_defect_risk", target_ref="src/cart.py",
        probability=0.3, confidence=0.4, model_name="random_forest", model_version="v1",
        dataset_version="test", feature_schema_version="v1", input_features=feature_sets[0],
        predicted_at=dt.datetime(2025, 1, 5),
    ))
    db.commit()
    db.close()
    return project["id"]


def test_digital_twin_graph_uses_real_rows_and_reconstructs_history(auth_headers):
    project_id = _create_twin_evidence(auth_headers, "Twin Evidence Project")
    response = client.get(f"/api/v1/twin/graph?project_id={project_id}", headers=auth_headers)
    assert response.status_code == 200
    graph = response.json()
    module_nodes = [node for node in graph["nodes"] if node["node_type"] == "module"]
    assert len(module_nodes) == 2
    checkout = next(node for node in module_nodes if node["attributes"]["path"] == "src/checkout.py")
    assert checkout["attributes"]["loc"] == 180
    assert checkout["attributes"]["risk"] == 0.81
    edge_types = {edge["edge_type"] for edge in graph["edges"]}
    assert {"authorship", "co_change", "related_issue"} <= edge_types
    cochange = next(edge for edge in graph["edges"] if edge["edge_type"] == "co_change")
    assert cochange["evidence"]["commit_count"] == 2
    assert any(node["node_type"] == "task" for node in graph["nodes"])

    historical = client.get("/api/v1/twin/snapshot", params={
        "project_id": project_id, "at": "2025-02-01T00:00:00",
    }, headers=auth_headers)
    assert historical.status_code == 200
    assert historical.json()["source"] == "reconstructed_from_predictions_and_commits"
    assert len([node for node in historical.json()["nodes"] if node["node_type"] == "module"]) == 2


def test_twin_reports_empty_when_project_has_no_repository(auth_headers):
    project = client.post("/api/v1/projects", json={"name": "No Repository"}, headers=auth_headers).json()
    response = client.get(f"/api/v1/twin/graph?project_id={project['id']}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["empty"] is True
    assert response.json()["nodes"] == []
    assert response.json()["edges"] == []


def test_counterfactual_uses_champion_without_persisting_prediction(auth_headers):
    project_id = _create_twin_evidence(auth_headers, "Simulation Project")
    module = next(row for row in client.get(
        f"/api/v1/code-risk?project_id={project_id}", headers=auth_headers,
    ).json() if row["path"] == "src/checkout.py")
    assert module["num_functions"] == 8
    assert module["historical_defects"] == 1
    before = client.get(
        f"/api/v1/predictions?project_id={project_id}&target_type=module_defect_risk",
        headers=auth_headers,
    ).json()
    response = client.post("/api/v1/predictions/simulate", json={
        "project_id": project_id,
        "module_path": "src/checkout.py",
        "cyclomatic_complexity": 3,
        "test_coverage": 0.9,
    }, headers=auth_headers)
    assert response.status_code == 200
    simulation = response.json()
    assert simulation["is_simulation"] is True
    assert simulation["label"] == "hypothetical - not a real observation"
    assert 0 <= simulation["probability"] <= 1
    assert simulation["shap_values"]
    assert simulation["features"]["cyclomatic_complexity"] == 3
    after = client.get(
        f"/api/v1/predictions?project_id={project_id}&target_type=module_defect_risk",
        headers=auth_headers,
    ).json()
    assert len(after) == len(before)


def test_blast_radius_requires_real_cochange_evidence(auth_headers):
    project_id = _create_twin_evidence(auth_headers, "Blast Radius Project")
    response = client.get(
        f"/api/v1/code-risk/src%2Fcheckout.py/blast-radius?project_id={project_id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["module_path"] == "src/cart.py"
    assert results[0]["cochange_count"] == 2
    assert results[0]["recent_joint_commits"] == 2
    assert results[0]["propagated_risk_contribution"] > 0


def test_memory_timeline_summarizes_real_risk_delta(auth_headers):
    project_id = _create_twin_evidence(auth_headers, "Memory Project")
    response = client.get(f"/api/v1/memory/timeline?project_id={project_id}", headers=auth_headers)
    assert response.status_code == 200
    summaries = [entry["summary"] for entry in response.json()["timeline"]]
    assert any("rose from 62% to 81%" in summary for summary in summaries)
    assert any("churn increased from 12 to 36" in summary for summary in summaries)


def test_fusion_recalibration_requires_minimum_measured_sample(auth_headers):
    project = client.post("/api/v1/projects", json={"name": "Calibration Project"}, headers=auth_headers).json()
    action = "Review module and add targeted unit tests"
    db = SessionLocal()
    for index in range(4):
        db.add(Recommendation(
            project_id=project["id"], recommended_action=action, reason="Measured outcome",
            evidence={}, priority="high", responsible_role="DEVELOPER", related_ref="src/checkout.py",
            status="completed", outcome="risk_decreased", risk_before=0.8, risk_after=0.5,
            effectiveness_score=0.3,
        ))
    db.commit()
    db.close()

    refused = client.post("/api/v1/models/recalibrate?minimum_samples=1000", headers=auth_headers)
    assert refused.status_code == 422
    assert "At least 1000" in refused.json()["detail"]
    below_minimum = client.post("/api/v1/models/recalibrate?minimum_samples=1", headers=auth_headers)
    assert below_minimum.status_code == 422
    assert "between 5 and 1000" in below_minimum.json()["detail"]

    db = SessionLocal()
    db.add(Recommendation(
        project_id=project["id"], recommended_action=action, reason="Measured outcome",
        evidence={}, priority="high", responsible_role="DEVELOPER", related_ref="src/checkout.py",
        status="completed", outcome="risk_increased", risk_before=0.3, risk_after=0.5,
        effectiveness_score=-0.2,
    ))
    db.commit()
    db.close()
    calibrated = client.post("/api/v1/models/recalibrate", headers=auth_headers)
    assert calibrated.status_code == 200
    body = calibrated.json()
    assert body["sample_size"] >= 5
    assert body["version"] == 1
    assert body["weights"][action] != 1.0
    history = client.get("/api/v1/models/fusion-weights", headers=auth_headers)
    assert history.status_code == 200
    assert history.json()["history"][0]["version"] == 1
