"""Usage / budget tracking tests."""
from sqlalchemy import select

from app.models import LLMSetting, UsageRecord


def test_usage_summary_empty(client):
    s = client.get("/usage/summary").json()
    assert s["total_cost"] == 0
    assert s["record_count"] == 0


def test_usage_summary_reflects_records(client, db):
    db.add(UsageRecord(kind="llm", provider="openrouter", model="test/model", tokens=100, cost=0.5))
    db.add(UsageRecord(kind="media", provider="openrouter", model="img", cost=0.25))
    db.commit()

    s = client.get("/usage/summary").json()
    assert s["total_cost"] == 0.75
    assert s["llm_cost"] == 0.5
    assert s["media_cost"] == 0.25
    assert s["record_count"] == 2

    records = client.get("/usage").json()
    assert len(records) == 2
    assert records[0]["cost"] in (0.5, 0.25)


def test_usage_budget_remaining(client, db):
    from sqlalchemy import delete

    db.execute(delete(UsageRecord))  # isolate from prior tests' records
    db.commit()

    setting = db.scalar(select(LLMSetting).limit(1))
    if setting is None:
        setting = LLMSetting(provider="openrouter", monthly_limit=100.0)
        db.add(setting)
        db.commit()
    else:
        setting.monthly_limit = 100.0
        db.commit()

    db.add(UsageRecord(kind="llm", provider="openrouter", model="m", cost=30.0))
    db.commit()

    s = client.get("/usage/summary").json()
    assert s["monthly_limit"] == 100.0
    assert s["remaining"] == 70.0


def test_usage_cost_extraction():
    """usage.cost is read as a float (credits), tolerant of absent/malformed values."""
    from app.llm import _usage_cost

    assert _usage_cost({"cost": 0.0042}) == 0.0042
    assert _usage_cost({"cost": "0.01"}) == 0.01
    assert _usage_cost({"cost": 3}) == 3.0
    assert _usage_cost({}) == 0.0
    assert _usage_cost({"cost": "nope"}) == 0.0


def test_usage_summary_all_time(client, db):
    """total_cost = this month only; total_all_time = ever."""
    import datetime
    from sqlalchemy import delete

    from app.auth import utcnow

    db.execute(delete(UsageRecord))
    db.commit()
    old = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(days=1)
    db.add(UsageRecord(kind="llm", provider="openrouter", model="m", cost=5.0, created_at=old))
    db.add(UsageRecord(kind="llm", provider="openrouter", model="m", cost=1.0))
    db.commit()

    s = client.get("/usage/summary").json()
    assert s["total_cost"] == 1.0        # this month only
    assert s["total_all_time"] == 6.0    # ever


def test_usage_export_csv(client, db):
    """CSV export returns the usage records with a header."""
    from sqlalchemy import delete

    db.execute(delete(UsageRecord))
    db.commit()
    db.add(UsageRecord(kind="llm", provider="openrouter", model="m/test", tokens=10, cost=0.5))
    db.commit()

    r = client.get("/usage/export")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "date" in r.text and "m/test" in r.text and "0,500000" in r.text

    # month filter
    r2 = client.get("/usage/export?month=1999-01")
    assert r2.status_code == 200
    assert "m/test" not in r2.text  # old month has no records



