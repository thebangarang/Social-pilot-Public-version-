"""Usage metering — the billing-grade ledger (app/usage_meter.py + GET /usage).

Two layers:
  * pure pricing/token-split unit tests (no DB, no network);
  * an API test on the management aggregate endpoint, with rows written through
    ``record_usage`` (never through the provider).
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from app.models import UsageRecord
from app import usage_meter
from app.usage_meter import PriceMap, Tokens, extract_tokens, price_call, record_usage, unknown_price_rows

# DeepSeek peak windows are 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri.
PEAK = datetime(2026, 9, 24, 7, 0, tzinfo=timezone.utc)      # Thursday 07:00 UTC
OFF_PEAK = datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc)  # Thursday 20:00 UTC

ROW_KEYS = {
    "day", "provider", "model", "tokens_in", "tokens_out",
    "tokens_cached", "cost_usd", "cost", "price_source", "calls", "currency",
}


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #
def test_extract_tokens_openai_shape():
    t = extract_tokens({"prompt_tokens": 1000, "completion_tokens": 250, "total_tokens": 1250})
    assert (t.input, t.output, t.cached) == (1000, 250, 0)
    assert t.total == 1250
    assert t.billable_input == 1000  # no cache split reported → billed uncached


def test_extract_tokens_openrouter_cache_detail():
    t = extract_tokens({
        "prompt_tokens": 1000, "completion_tokens": 100,
        "prompt_tokens_details": {"cached_tokens": 900},
    })
    assert (t.input, t.cached) == (1000, 900)
    assert t.billable_input == 100


def test_extract_tokens_deepseek_hit_miss():
    """DeepSeek reports cache hit/miss instead of prompt_tokens."""
    t = extract_tokens({"prompt_cache_hit_tokens": 900, "prompt_cache_miss_tokens": 100,
                        "completion_tokens": 50})
    assert (t.input, t.cached, t.output) == (1000, 900, 50)
    assert t.billable_input == 100


def test_extract_tokens_tolerates_junk():
    assert extract_tokens(None).total == 0
    assert extract_tokens({}).total == 0
    assert extract_tokens({"prompt_tokens": "nope"}).total == 0


# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #
def test_provider_cost_always_wins():
    """Even for a model the pinned map does not know."""
    p = price_call("openrouter", "vendor/never-heard-of-it", Tokens(input=10, output=5),
                   provider_cost=0.0042)
    assert p.source == "provider"
    assert p.cost == 0.0042
    assert p.billable


def test_provider_cost_zero_is_free_not_unknown():
    """0.0 from the provider means 'this call was free' — not 'we do not know'."""
    p = price_call("openrouter", "vendor/never-heard-of-it", Tokens(input=10),
                   provider_cost=0.0)
    assert p.source == "provider"
    assert p.cost == 0.0


def test_pinned_price_covers_openai_compatible_deepseek():
    """DeepSeek direct (openai_compatible) returns no cost → must be pinned, non-zero."""
    p = price_call("openai_compatible", "deepseek-flash",
                   Tokens(input=1000, output=500), at=PEAK)
    assert p.source == "pinned"
    assert p.cost == pytest.approx(0.0009)  # (1000*0.3 + 500*1.2) / 1M
    assert p.cost > 0


def test_pinned_entry_is_an_alias_of_the_canonical_rate_table():
    prices = PriceMap.load()
    entry, key = prices.lookup("openai_compatible", "deepseek-flash")
    assert key == "deepseek:deepseek-flash"
    assert entry is prices.models["deepseek:deepseek-flash"]


def test_unknown_model_is_marked_unknown_not_silently_zero():
    p = price_call("openai_compatible", "vendor/mystery-model", Tokens(input=5000, output=5000))
    assert p.source == "unknown"
    assert p.billable is False
    assert "mystery-model" in p.note
    # The statement guard (unknown_price_rows) must see it and refuse to bill.
    row = type("Row", (), {"price_source": "unknown"})()
    assert unknown_price_rows([row]) == [row]


def test_off_peak_is_half_of_peak_for_deepseek():
    tokens = Tokens(input=10_000, output=2_000)
    peak = price_call("openai_compatible", "deepseek-flash", tokens, at=PEAK)
    off = price_call("openai_compatible", "deepseek-flash", tokens, at=OFF_PEAK)
    assert peak.cost == pytest.approx(0.0054)  # (10k*0.3 + 2k*1.2) / 1M
    assert off.cost == pytest.approx(peak.cost / 2)
    assert "peak" in peak.note and "off_peak" in off.note


def test_cached_input_is_billed_at_the_cached_rate():
    """Cached tokens are billed separately from the uncached input — never the reverse."""
    cached = price_call("openai_compatible", "deepseek-flash",
                        Tokens(input=100_000, cached=100_000), at=PEAK)
    uncached = price_call("openai_compatible", "deepseek-flash",
                          Tokens(input=100_000), at=PEAK)
    assert cached.cost == pytest.approx(0.0006)   # 100k * 0.006 / 1M
    assert uncached.cost == pytest.approx(0.03)   # 100k * 0.3 / 1M
    assert cached.cost < uncached.cost


def test_local_inference_is_pinned_free_but_still_counted():
    p = price_call("ollama", "llama3.2", Tokens(input=1000, output=1000))
    assert p.source == "pinned"
    assert p.cost == 0.0
    assert p.billable  # priced (free), not "unknown"


# --------------------------------------------------------------------------- #
# Ledger write
# --------------------------------------------------------------------------- #
def test_record_usage_writes_the_split_and_the_provenance(db):
    db.execute(delete(UsageRecord))
    db.commit()

    row = record_usage(db, UsageRecord, kind="llm", provider="openai_compatible",
                       model="deepseek-flash", at=PEAK,
                       usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500})
    assert (row.tokens, row.tokens_in, row.tokens_out, row.tokens_cached) == (1500, 1000, 500, 0)
    assert row.price_source == "pinned"
    assert row.currency == "USD"
    assert row.cost == pytest.approx(0.0009)
    assert row.price_note


# --------------------------------------------------------------------------- #
# GET /api/v1/management/usage
# --------------------------------------------------------------------------- #
def _seed(db):
    """Three real call shapes, two days: pinned, provider-priced, unknown-priced."""
    db.execute(delete(UsageRecord))
    db.commit()

    pinned = record_usage(
        db, UsageRecord, kind="llm", provider="openai_compatible", model="deepseek-flash", at=PEAK,
        usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
    )
    provided = record_usage(
        db, UsageRecord, kind="llm", provider="openrouter", model="vendor/model-a",
        usage={"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
        provider_cost=0.25,
    )
    unpriced = record_usage(
        db, UsageRecord, kind="media", provider="openrouter", model="vendor/mystery-image",
    )
    pinned.created_at = datetime(2026, 9, 10, 12, 0)
    provided.created_at = datetime(2026, 9, 10, 18, 0)
    unpriced.created_at = datetime(2026, 9, 11, 9, 0)
    db.commit()
    return pinned, provided, unpriced


def _usage(client, token, **params):
    return client.get("/api/v1/management/usage", headers={"X-Management-Token": token}, params=params)


def test_management_usage_requires_the_management_token(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "management_token", "secret-token")
    assert client.get("/api/v1/management/usage").status_code == 401
    assert client.get("/api/v1/management/usage",
                      headers={"X-Management-Token": "wrong"}).status_code == 401

    monkeypatch.setattr(settings, "management_token", "")
    assert client.get("/api/v1/management/usage",
                      headers={"X-Management-Token": "secret-token"}).status_code == 404


def test_management_usage_aggregates_the_ledger(client, db, monkeypatch):
    from app.auth import utcnow
    from app.config import settings

    monkeypatch.setattr(settings, "management_token", "secret-token")
    try:
        _seed(db)
        r = _usage(client, "secret-token", since="2026-09-01", until="2026-09-30")
        assert r.status_code == 200
        body = r.json()

        assert body["since"] == "2026-09-01"
        assert body["until"] == "2026-09-30"
        assert body["currency"] == "USD"
        assert body["calls"] == 3
        assert body["total_tokens"] == 1800
        assert body["total_cost_usd"] == pytest.approx(0.2509)
        assert body["unpriced_rows"] == 1
        assert body["instance_id"] and body["label"]

        # Aggregates only — no prompt, no output, no operator field ever leaves.
        assert len(body["rows"]) == 3
        for row in body["rows"]:
            assert set(row) == ROW_KEYS
        assert "prompt" not in str(body) and "@" not in str(body)

        by_model = {row["model"]: row for row in body["rows"]}
        assert by_model["deepseek-flash"]["day"] == "2026-09-10"
        assert by_model["deepseek-flash"]["price_source"] == "pinned"
        assert by_model["deepseek-flash"]["tokens_in"] == 1000
        assert by_model["deepseek-flash"]["tokens_out"] == 500
        assert by_model["deepseek-flash"]["cost_usd"] == pytest.approx(0.0009)
        assert by_model["vendor/model-a"]["price_source"] == "provider"
        assert by_model["vendor/model-a"]["cost_usd"] == pytest.approx(0.25)
        assert by_model["vendor/mystery-image"]["price_source"] == "unknown"
        assert by_model["vendor/mystery-image"]["calls"] == 1
    finally:
        db.execute(delete(UsageRecord))
        db.commit()

    # Default window is the current month, and an empty ledger aggregates to zero.
    now = utcnow()
    body = _usage(client, "secret-token").json()
    assert body["since"] == now.replace(day=1).strftime("%Y-%m-%d")
    assert body["until"] == now.strftime("%Y-%m-%d")
    assert body["rows"] == []


def test_management_usage_groups_and_filters(client, db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "management_token", "secret-token")
    try:
        _seed(db)
        # `until` is inclusive: the 11th only holds the unpriced media row.
        one_day = _usage(client, "secret-token", since="2026-09-11", until="2026-09-11").json()
        assert one_day["calls"] == 1
        assert one_day["unpriced_rows"] == 1

        by_provider = _usage(client, "secret-token", since="2026-09-01", until="2026-09-30",
                             group_by="day,provider").json()
        assert len(by_provider["rows"]) == 3
        assert all(row["model"] is None for row in by_provider["rows"])

        # A window before the rows existed stays empty.
        empty = _usage(client, "secret-token", since="2026-08-01", until="2026-08-31").json()
        assert (empty["calls"], empty["total_cost_usd"], empty["rows"]) == (0, 0.0, [])

        assert _usage(client, "secret-token", group_by="nope").status_code == 400
        assert _usage(client, "secret-token", since="10/09/2026").status_code == 400
    finally:
        db.execute(delete(UsageRecord))
        db.commit()


def test_management_usage_is_additive_to_the_existing_contract(client, db, monkeypatch):
    """/stats and /health keep working unchanged next to the new /usage."""
    from app.config import settings

    monkeypatch.setattr(settings, "management_token", "secret-token")
    try:
        _seed(db)
        stats = client.get("/api/v1/management/stats",
                           headers={"X-Management-Token": "secret-token"})
        assert stats.status_code == 200
        assert stats.json()["usage_all_time_usd"] == pytest.approx(0.2509)
        assert stats.json()["tokens_all_time"] == 1800
        assert client.get("/api/v1/management/health",
                          headers={"X-Management-Token": "secret-token"}).json()["status"] == "ok"
    finally:
        db.execute(delete(UsageRecord))
        db.commit()


def test_usage_records_table_has_the_meter_columns():
    """The migration is additive: every new column exists and is nullable."""
    from sqlalchemy import inspect

    from app.database import engine

    cols = {c["name"]: c for c in inspect(engine).get_columns("usage_records")}
    for name in ("tokens_in", "tokens_out", "tokens_cached", "price_source", "price_note", "currency"):
        assert name in cols, f"usage_records.{name} missing"
        assert cols[name]["nullable"], f"usage_records.{name} must stay nullable (legacy rows)"
    for legacy in ("kind", "provider", "model", "tokens", "cost", "created_at"):
        assert legacy in cols


def test_legacy_rows_do_not_break_the_aggregate(client, db, monkeypatch):
    """Rows written before the meter (NULL provenance) still aggregate."""
    from app.config import settings

    monkeypatch.setattr(settings, "management_token", "secret-token")
    try:
        db.execute(delete(UsageRecord))
        db.add(UsageRecord(kind="llm", provider="openrouter", model="legacy/model",
                           tokens=42, cost=0.5, created_at=datetime(2026, 9, 12, 8, 0)))
        db.commit()

        body = _usage(client, "secret-token", since="2026-09-12", until="2026-09-12").json()
        assert body["calls"] == 1
        assert body["total_cost_usd"] == pytest.approx(0.5)
        assert body["total_tokens"] == 42            # falls back to the legacy `tokens`
        assert body["rows"][0]["price_source"] == "unknown"
        # A row with NO provenance is unpriced: we cannot tell a provider-reported
        # figure from a silent zero, so a statement must refuse it until the
        # migration backfills it (cost > 0 -> 'provider', cost = 0 -> 'unknown').
        assert body["unpriced_rows"] == 1
    finally:
        db.execute(delete(UsageRecord))
        db.commit()


def test_legacy_backfill_makes_history_billable_or_loud(client, db, monkeypatch):
    """The one-time backfill: cost > 0 becomes 'provider', a bare zero becomes 'unknown'."""
    from sqlalchemy import text

    from app.config import settings

    monkeypatch.setattr(settings, "management_token", "secret-token")
    try:
        db.execute(delete(UsageRecord))
        db.add(UsageRecord(kind="llm", provider="openrouter", model="legacy/paid",
                           tokens=100, cost=0.25, created_at=datetime(2026, 9, 13, 8, 0)))
        db.add(UsageRecord(kind="llm", provider="openrouter", model="legacy/zero",
                           tokens=10, cost=0.0, created_at=datetime(2026, 9, 13, 9, 0)))
        db.commit()

        before = _usage(client, "secret-token", since="2026-09-13", until="2026-09-13").json()
        assert before["unpriced_rows"] == 2          # both rows unknown before the backfill

        for statement in usage_meter.legacy_backfill_statements("usage_records"):
            db.execute(text(statement))
        db.commit()

        after = _usage(client, "secret-token", since="2026-09-13", until="2026-09-13").json()
        sources = {r["model"]: r["price_source"] for r in after["rows"]}
        assert sources["legacy/paid"] == "provider"  # the old code read the provider's cost
        assert sources["legacy/zero"] == "unknown"   # a zero with no provenance proves nothing
        assert after["unpriced_rows"] == 1           # only the unproven one stays unbillable
    finally:
        db.execute(delete(UsageRecord))
        db.commit()


def test_ledger_rows_written_through_record_usage_are_queryable(client, db, monkeypatch):
    """End-to-end: record_usage → DB → /usage (no network anywhere)."""
    from app.config import settings

    monkeypatch.setattr(settings, "management_token", "secret-token")
    try:
        db.execute(delete(UsageRecord))
        db.commit()
        record_usage(db, UsageRecord, kind="llm", provider="ollama", model="llama3.2",
                     usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

        body = _usage(client, "secret-token").json()  # default: current month
        assert body["calls"] == 1
        assert body["rows"][0]["price_source"] == "pinned"
        assert body["rows"][0]["cost_usd"] == 0.0      # local inference: priced, free
        assert body["rows"][0]["day"] == body["until"]
        assert body["unpriced_rows"] == 0
        assert body["total_tokens"] == 15
        assert db.scalar(select(UsageRecord).order_by(UsageRecord.id.desc())).price_source == "pinned"
    finally:
        db.execute(delete(UsageRecord))
        db.commit()


# --------------------------------------------------------------------------- #
# OVHcloud AI Endpoints — adopted sovereign provider (class A, EUR, no off-peak)
# --------------------------------------------------------------------------- #
def test_ovh_prices_are_pinned_in_euros_with_a_flat_band():
    prices = PriceMap.load()
    tokens = Tokens(input=1_000_000, output=500_000)
    p = price_call("ovh", "gpt-oss-120b", tokens, at=PEAK, prices=prices)
    assert p.cost == pytest.approx(0.28)      # 1M * 0.08 + 0.5M * 0.4
    assert p.currency == "EUR"
    assert p.source == "pinned"
    # OVH has no off-peak concept: the note must not read "peak" as if a band was applied.
    assert p.note.endswith("flat")
    assert price_call("ovh", "gpt-oss-120b", tokens, at=OFF_PEAK, prices=prices).cost == p.cost


def test_ovh_embedding_model_prices_input_only():
    p = price_call("ovh", "bge-m3", Tokens(input=2_000_000), prices=PriceMap.load())
    assert p.cost == pytest.approx(0.02)
    assert p.source == "pinned"
    assert p.currency == "EUR"


def test_unpriced_ovh_model_is_unknown_not_zero():
    """A beta/free or newly added OVH model must be loud, not silently free."""
    p = price_call("ovh", "some-model-ovh-added-yesterday", Tokens(input=1000, output=1000))
    assert p.source == "unknown"
    assert p.billable is False


def test_ovh_row_carries_its_currency_end_to_end(client, db, monkeypatch):
    """Currency travels with the row: EUR here, USD for OpenRouter."""
    from app.config import settings

    monkeypatch.setattr(settings, "management_token", "secret-token")
    try:
        db.execute(delete(UsageRecord))
        db.commit()
        row = record_usage(
            db, UsageRecord, kind="llm", provider="ovh", model="gpt-oss-120b",
            usage={"prompt_tokens": 1_000_000, "completion_tokens": 500_000},
        )
        assert row.price_source == "pinned"
        assert row.currency == "EUR"
        assert row.cost == pytest.approx(0.28)
        assert row.tokens_in == 1_000_000 and row.tokens_out == 500_000

        body = _usage(client, "secret-token").json()
        assert body["currency"] == "EUR"
        assert body["unpriced_rows"] == 0
        assert body["rows"][0]["model"] == "gpt-oss-120b"
    finally:
        db.execute(delete(UsageRecord))
        db.commit()


def test_each_row_carries_its_own_currency(client, db, monkeypatch):
    """A EUR source and a USD source in the same period must not be summed together."""
    from app.config import settings

    monkeypatch.setattr(settings, "management_token", "secret-token")
    try:
        db.execute(delete(UsageRecord))
        db.commit()
        record_usage(db, UsageRecord, kind="llm", provider="ovh", model="gpt-oss-120b",
                     usage={"prompt_tokens": 1_000_000, "completion_tokens": 0})
        record_usage(db, UsageRecord, kind="llm", provider="openrouter", model="anthropic/claude-sonnet-4",
                     usage={"prompt_tokens": 1000, "completion_tokens": 500}, provider_cost=0.0042)

        body = _usage(client, "secret-token").json()
        by_model = {r["model"]: r for r in body["rows"]}
        assert by_model["gpt-oss-120b"]["currency"] == "EUR"
        assert by_model["anthropic/claude-sonnet-4"]["currency"] == "USD"
        assert body["currency"] == "mixed"      # never silently one total
    finally:
        db.execute(delete(UsageRecord))
        db.commit()


def test_each_row_exposes_its_cost_under_the_honest_name(client, db, monkeypatch):
    """`cost` is the currency-correct name; `cost_usd` stays for compatibility."""
    from app.config import settings

    monkeypatch.setattr(settings, "management_token", "secret-token")
    try:
        db.execute(delete(UsageRecord)); db.commit()
        record_usage(db, UsageRecord, kind="llm", provider="ovh", model="gpt-oss-120b",
                     usage={"prompt_tokens": 1_000_000, "completion_tokens": 0})
        row = _usage(client, "secret-token").json()["rows"][0]
        assert row["cost"] == row["cost_usd"] == pytest.approx(0.08)
        assert row["currency"] == "EUR"
    finally:
        db.execute(delete(UsageRecord)); db.commit()
