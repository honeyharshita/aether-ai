import os
import sys
import datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.recommend import score_task_priority
from app.models import Task


def _make_task(**overrides):
    defaults = dict(
        id="t1", project_id="p1", title="Test task", story_points=3,
        blocking_count=0, deadline=None, created_at=dt.datetime.utcnow(),
    )
    defaults.update(overrides)
    t = Task()
    for k, v in defaults.items():
        setattr(t, k, v)
    return t


def test_priority_increases_with_defect_risk():
    task = _make_task()
    low = score_task_priority(task, defect_risk=0.1, security_risk=0.0)
    high = score_task_priority(task, defect_risk=0.9, security_risk=0.0)
    assert high["score"] > low["score"]


def test_priority_increases_with_near_deadline():
    far = _make_task(deadline=dt.datetime.utcnow() + dt.timedelta(days=60))
    near = _make_task(deadline=dt.datetime.utcnow() + dt.timedelta(days=1))
    far_score = score_task_priority(far, defect_risk=0.0, security_risk=0.0)
    near_score = score_task_priority(near, defect_risk=0.0, security_risk=0.0)
    assert near_score["score"] > far_score["score"]


def test_priority_score_bounded_0_100():
    task = _make_task(blocking_count=100, deadline=dt.datetime.utcnow())
    result = score_task_priority(task, defect_risk=1.0, security_risk=1.0)
    assert 0 <= result["score"] <= 100


def test_priority_reasons_reflect_drivers():
    task = _make_task(deadline=dt.datetime.utcnow() + dt.timedelta(days=1), blocking_count=3)
    result = score_task_priority(task, defect_risk=0.9, security_risk=0.0)
    assert any("deadline" in r for r in result["reasons"])
    assert any("defect risk" in r for r in result["reasons"])
