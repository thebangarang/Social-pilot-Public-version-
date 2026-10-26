#!/usr/bin/env python3
"""Initialize the SQLite schema and seed built-in themes (idempotent)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text

import app.models  # noqa: F401  (register models on Base.metadata)
from app import usage_meter
from app.database import Base, SessionLocal, engine
from app.main import DEFAULT_THEMES
from app.models import Theme

Base.metadata.create_all(bind=engine)


def _ensure_column(table: str, column: str, ddl: str) -> None:
    """Add a column if missing (SQLite has no ADD COLUMN IF NOT EXISTS)."""
    from sqlalchemy import inspect, text

    cols = {c["name"] for c in inspect(engine).get_columns(table)}
    if column not in cols:
        with engine.begin() as conn:
            conn.execute(text(ddl))
        print(f"Added column {table}.{column}")


_ensure_column("operators", "can_publish", "ALTER TABLE operators ADD COLUMN can_publish BOOLEAN NOT NULL DEFAULT 0")
_ensure_column("brands", "logo_path", "ALTER TABLE brands ADD COLUMN logo_path VARCHAR(255)")
_ensure_column("chat_messages", "media_brief", "ALTER TABLE chat_messages ADD COLUMN media_brief TEXT")
_ensure_column("chat_messages", "platforms", "ALTER TABLE chat_messages ADD COLUMN platforms JSON")
_ensure_column("posts", "media_brief", "ALTER TABLE posts ADD COLUMN media_brief TEXT")
_ensure_column("posts", "media_variants", "ALTER TABLE posts ADD COLUMN media_variants JSON")
# AI Act Article 50(2) provenance: whether an asset came from the generator, and whether the durable
# mark is in it. Existing rows are hand-uploaded or newer than the generator, so the defaults (not
# generated, not marked) are correct for them — the publish gate only refuses ai_generated rows.
_ensure_column("media_items", "ai_generated",
               "ALTER TABLE media_items ADD COLUMN ai_generated BOOLEAN NOT NULL DEFAULT 0")
_ensure_column("media_items", "ai_marking",
               "ALTER TABLE media_items ADD COLUMN ai_marking VARCHAR(20) NOT NULL DEFAULT 'none'")
# Billing-grade ledger columns (app/usage_meter): token split + price provenance.
# Existing rows keep their cost/tokens; their new columns stay NULL.
_ensure_column("usage_records", "tokens_in", "ALTER TABLE usage_records ADD COLUMN tokens_in INTEGER")
_ensure_column("usage_records", "tokens_out", "ALTER TABLE usage_records ADD COLUMN tokens_out INTEGER")
_ensure_column("usage_records", "tokens_cached", "ALTER TABLE usage_records ADD COLUMN tokens_cached INTEGER")
_ensure_column("usage_records", "price_source", "ALTER TABLE usage_records ADD COLUMN price_source VARCHAR(16)")
_ensure_column("usage_records", "price_note", "ALTER TABLE usage_records ADD COLUMN price_note VARCHAR(200)")
_ensure_column("usage_records", "currency", "ALTER TABLE usage_records ADD COLUMN currency VARCHAR(8)")

# Provenance backfill, once, for rows written before the meter existed. NULL means
# "we cannot tell a provider-reported figure from a silent zero", so history would
# otherwise be billable-or-not at random. `cost > 0` came from the provider's own
# field (the pre-meter code read it); a zero with no provenance proves nothing.
from sqlalchemy import text  # noqa: E402

for _stmt in usage_meter.legacy_backfill_statements("usage_records"):
    with engine.begin() as _conn:
        _result = _conn.execute(text(_stmt))
        if _result.rowcount:
            print(f"Backfilled price_source on {_result.rowcount} row(s): {_stmt.split('SET ')[1].split(' WHERE')[0]}")

db = SessionLocal()
try:
    if db.scalar(select(Theme).limit(1)) is None:
        db.add_all(DEFAULT_THEMES)
        db.commit()
        print("Seeded built-in themes.")
finally:
    db.close()

print("DB initialized.")
