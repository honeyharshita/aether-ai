"""
Demo Project seeding (spec Section 47/77.11).

Creates a project explicitly flagged is_demo=True, with a reproducible
(fixed-seed) set of code modules, issues, tasks, and security findings,
then runs those modules through the SAME real prediction -> explanation
-> recommendation pipeline used for a real connected GitHub repository.
Nothing about the pipeline is faked here -- only the module *inputs* are
seeded rather than pulled from a live repo, and this is disclosed via
is_demo=True on every response.
"""
import datetime as dt
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Project, Repository, CodeModule, Issue, Task, SecurityFinding, Prediction, Explanation, User
from app.auth import get_current_user
from app.eventbus import publish
from app.ml.dataset import generate_dataset
from app.ml.explain import predict_defect_probability
from app.recommend import evaluate_module_recommendations

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])

MODULE_NAMES = [
    "PaymentService.java", "AuthController.java", "OrderRepository.java",
    "InventorySync.py", "NotificationWorker.py", "CheckoutFlow.tsx",
    "UserProfileService.java", "PricingEngine.py", "ShippingCalculator.java",
    "CartController.tsx", "RefundProcessor.java", "SearchIndexer.py",
]


@router.post("/seed")
def seed_demo_project(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = Project(name="AetherDemo", description="Reproducible seeded demo project", is_demo=True, owner_id=user.id)
    db.add(project)
    db.commit()
    db.refresh(project)
    publish(db, "project.created", project_id=project.id, source="demo-seeder", payload={"is_demo": True})

    repo = Repository(project_id=project.id, provider="demo", full_name="aetherai/demo-monorepo", default_branch="main")
    db.add(repo)
    db.commit()
    db.refresh(repo)
    publish(db, "repository.connected", project_id=project.id, source="demo-seeder", payload={"repository_id": repo.id})

    # Reuse the same fixed-seed feature generator used for ML training so
    # demo module metrics are drawn from a documented, reproducible
    # distribution rather than ad hoc numbers.
    feature_df = generate_dataset(n_samples=len(MODULE_NAMES), seed=99)

    modules = []
    predictions_out = []
    for i, name in enumerate(MODULE_NAMES):
        row = feature_df.iloc[i]
        mod = CodeModule(
            repository_id=repo.id, path=name, loc=int(row["loc"]),
            cyclomatic_complexity=float(row["cyclomatic_complexity"]),
            num_functions=int(row["num_functions"]), churn_30d=int(row["churn_30d"]),
            num_commits=int(row["num_commits"]), num_authors=int(row["num_authors"]),
            historical_defects=int(row["historical_defects"]), test_coverage=float(row["test_coverage"]),
        )
        db.add(mod)
        db.commit()
        db.refresh(mod)
        modules.append(mod)

        features = {
            "loc": mod.loc, "cyclomatic_complexity": mod.cyclomatic_complexity,
            "num_functions": mod.num_functions, "churn_30d": mod.churn_30d,
            "num_commits": mod.num_commits, "num_authors": mod.num_authors,
            "historical_defects": mod.historical_defects, "test_coverage": mod.test_coverage,
        }
        result = predict_defect_probability(features)
        pred = Prediction(
            project_id=project.id, target_type="module_defect_risk", target_ref=mod.path,
            probability=result["probability"], confidence=result["confidence"],
            model_name=result["model_name"], model_version=result["model_version"],
            dataset_version=result["dataset_version"], feature_schema_version=result["feature_schema_version"],
            input_features=features,
        )
        db.add(pred)
        db.commit()
        db.refresh(pred)
        publish(db, "model.prediction.created", project_id=project.id, source="ai-ml-service",
                payload={"module": mod.path, "probability": result["probability"]})

        expl = Explanation(
            prediction_id=pred.id, shap_values=result["shap_values"],
            top_positive_factors=result["top_positive_factors"],
            top_negative_factors=result["top_negative_factors"],
            explanation_text=result["explanation_text"],
        )
        db.add(expl)
        db.commit()

        recs = evaluate_module_recommendations(db, project.id, mod, pred, expl)
        for r in recs:
            publish(db, "recommendation.created", project_id=project.id, source="recommendation-service",
                    payload={"recommendation_id": r.id, "action": r.recommended_action, "module": mod.path})

        predictions_out.append({"module": mod.path, "probability": result["probability"]})

    # Seed a handful of real issues, tasks, and security findings so
    # downstream pages (Bugs, Sprints, Security) have real rows to render.
    issues = [
        Issue(project_id=project.id, source="internal", title="Checkout fails on discount code edge case",
              issue_type="bug", severity="high", related_module_path="CheckoutFlow.tsx"),
        Issue(project_id=project.id, source="internal", title="Refund double-processed under race condition",
              issue_type="bug", severity="critical", related_module_path="RefundProcessor.java"),
        Issue(project_id=project.id, source="internal", title="Search index lag during peak load",
              issue_type="bug", severity="medium", related_module_path="SearchIndexer.py"),
    ]
    db.add_all(issues)

    tasks = [
        Task(project_id=project.id, title="Add integration tests for RefundProcessor", story_points=5,
             blocking_count=2, deadline=dt.datetime.utcnow() + dt.timedelta(days=3)),
        Task(project_id=project.id, title="Refactor PricingEngine discount logic", story_points=8,
             blocking_count=1, deadline=dt.datetime.utcnow() + dt.timedelta(days=10)),
        Task(project_id=project.id, title="Investigate SearchIndexer lag", story_points=3,
             blocking_count=0, deadline=dt.datetime.utcnow() + dt.timedelta(days=14)),
    ]
    db.add_all(tasks)

    findings = [
        SecurityFinding(project_id=project.id, source="trivy", severity="critical",
                         cve="CVE-2024-12345", component="log4j-core:2.14.1", status="open"),
        SecurityFinding(project_id=project.id, source="sonarqube", severity="medium",
                         component="AuthController.java", status="open"),
    ]
    db.add_all(findings)
    db.commit()

    return {
        "project_id": project.id, "is_demo": True,
        "modules_seeded": len(modules), "predictions": predictions_out,
        "issues_seeded": len(issues), "tasks_seeded": len(tasks), "security_findings_seeded": len(findings),
    }
