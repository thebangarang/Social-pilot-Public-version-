#!/usr/bin/env python3
"""Bring a live database's schema up to the models — additively, never destructively.

Why this exists: on 2026-09-27 the first promotion through the release pipeline put new code in
front of a database created by the old code, and `/api/v1/management/usage` answered 500 —
`no such column: usage_records.tokens_in`. No ring could catch it: a devnet database is created
*by the new code*, so it always has the new shape. Code/data drift is a migration, and nothing in
the pipeline looked at migrations at all.

What it does: compares every table and column the models declare against the live database and
emits what is missing (CREATE TABLE / ALTER TABLE ADD COLUMN). Additive only — it never drops,
never renames, never rewrites an existing column. A NOT NULL column with no default is added as
nullable, and said so out loud, because sqlite cannot add it any other way.

Run it on the instance that has the old data, after a backup and before the health checks:

    docker exec -i socialpilot-app python - < scripts/sync_schema.py            # --dry-run to look
"""
from __future__ import annotations

import os
import sqlite3
import sys

from pathlib import Path

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateColumn, CreateTable

from app.models import Base

# The maintenance scripts are run as files, so the project root is not on the path by default.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.idents import safe_ident  # noqa: E402  (after the path insert, on purpose)

DRY = "--dry-run" in sys.argv


def database_path() -> Path:
    """The database this instance uses — the app's own setting, never a guess."""
    url = ""
    try:
        from app.config import settings  # noqa: PLC0415

        for attr in ("database_url", "DATABASE_URL", "db_url"):
            url = str(getattr(settings, attr, "") or "")
            if url:
                break
    except Exception:  # noqa: BLE001
        url = ""
    if not url and Path("/app/.env").exists():
        for line in Path("/app/.env").read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
    if not url:
        raise SystemExit("refusing: could not resolve DATABASE_URL from the app or its .env")
    if not url.startswith("sqlite"):
        raise SystemExit(f"refusing: only sqlite is handled here (got {url.split(':', 1)[0]})")
    return Path(url.split("///", 1)[1]).resolve()


def main() -> int:
    path = database_path()
    if not path.exists():
        raise SystemExit(f"no database at {path}")
    print(f"database: {path}")
    dialect = sqlite_dialect.dialect()
    con = sqlite3.connect(path)
    added = 0
    failures = 0
    notes: list[str] = []
    try:
        have_tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in Base.metadata.sorted_tables:
            if table.name not in have_tables:
                ddl = str(CreateTable(table).compile(dialect=dialect))
                print(f"CREATE TABLE {table.name}")
                if not DRY:
                    con.execute(ddl)
                added += 1
                continue
            # guard-ok: PRAGMA cannot take a bound parameter, so the name is proven to BE a name first
            have_cols = {r[1] for r in con.execute(f"PRAGMA table_info({safe_ident(table.name)})")}
            for column in table.columns:
                if column.name in have_cols:
                    continue
                # guard-ok: the table and column names are proven to be identifiers; the type is SQLAlchemy's
                # own compiled DDL, not a string from outside. Neither name can be a bound parameter.
                ddl = (f"ALTER TABLE {safe_ident(table.name)} ADD COLUMN "
                       f"{CreateColumn(column).compile(dialect=dialect)}")
                # Whether sqlite will refuse this column is knowable from the MODEL before the statement
                # runs: a NOT NULL column with no server default is the one case it rejects. In --dry-run the
                # statement never runs, so the old code could not reach its retry branch and the NULLABLE
                # downgrade — the single thing an operator needs from the preview — was unprintable there. A
                # second reviewer found that, and found that on a real run any operational error (a locked
                # database, an I/O failure) was labelled as that same downgrade, before the retry had even been
                # attempted.
                needs_nullable = (column.nullable is False and column.server_default is None
                                  and not column.primary_key)
                if DRY:
                    if needs_nullable:
                        note = (f"{table.name}.{column.name}: NOT NULL with no default — sqlite would refuse "
                                f"it and this script would add it NULLABLE; rows written before this column "
                                f"will read as NULL")
                        print(f"  ! {note}")
                        notes.append(note)
                    added += 1
                    continue
                try:
                    con.execute(ddl)
                except sqlite3.OperationalError as exc:
                    if not needs_nullable:
                        # Not the NOT NULL case: report the failure as itself. Calling a locked database a
                        # schema downgrade hides the real problem behind a plausible one.
                        print(f"✗ {table.name}.{column.name}: {exc}", file=sys.stderr)
                        failures += 1
                        continue
                    # guard-ok: both names are proven to be identifiers before they go near the statement
                    loose = (f"ALTER TABLE {safe_ident(table.name)} ADD COLUMN "
                             f"{safe_ident(column.name)} {column.type}")
                    con.execute(loose)          # the retry FIRST...
                    note = (f"{table.name}.{column.name}: added NULLABLE ({exc}) — rows written before "
                            f"this column will read as NULL; backfill deliberately if it matters")
                    print(f"  ! {note}")        # ...and the note only once it has actually happened
                    notes.append(note)
                added += 1
        if not DRY:
            con.commit()
    finally:
        con.close()
    print(f"✓ {'would add' if DRY else 'added'} {added} schema element(s)"
          + (f" · {failures} FAILED — see above" if failures else ""))
    for note in notes:
        print(f"  ! {note}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
