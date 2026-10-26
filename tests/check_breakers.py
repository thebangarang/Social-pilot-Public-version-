#!/usr/bin/env python3
"""R-ENG-022's mechanism for this project: run the breaker's cases, in their own database.

Why this is a script and not a manifest entry with an env file. The machine runs this check from the audit
(every fifteen minutes), from a promotion, and possibly while somebody is running the suite by hand — and the
suite's session fixture drops and recreates every table in the database it names. Two runs on one file delete
each other's schema and the loser reports "no such table: operators" for every test that touches it (measured:
86 failures, all auth, all of them from exactly that). So this check names its own database.

It sets that name here rather than in a committed `.env`-style file, because such a file holds `KEY=value`
lines, looks exactly like the credential files this repository is careful about, and is not one — the secret
scanner flagged it, correctly, and the honest fix is to remove the ambiguity rather than to record an
exception. `TEST_DB_NAME` (not `SP_TEST_DB_NAME`: `SP_` is this project's convention for actual secrets).

Two things the review of 70c8b2e asked for and got:

* **The name is set, not defaulted.** `setdefault` yields to an inherited value, so `TEST_DB_NAME=x` in the
  caller's environment would silently choose a different database — the one thing this script exists to
  prevent. It is assigned.
* **The database is asserted, not assumed.** The runner scores this check on its exit code alone, so a run
  that dropped and recreated tables in some other database would still report `✓`. The URL is computed and
  compared with the expected one before pytest starts, and a mismatch fails the check.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_NAME = "test-check.db"
CASES = PROJECT_ROOT / "tests" / "test_breaker.py"

sys.path.insert(0, str(PROJECT_ROOT))

from tests.db_path import session_db_url  # noqa: E402 — imported after the path is set, and that is why


def main() -> int:
    os.environ["TEST_DB_NAME"] = DB_NAME          # assigned, not defaulted: see the docstring

    expected = f"sqlite:///{PROJECT_ROOT / 'data' / DB_NAME}"
    url = session_db_url(PROJECT_ROOT, DB_NAME)
    if url != expected:
        print(f"check_breakers: this check must run against {expected}, but the suite would use {url}",
              file=sys.stderr)
        return 1

    print(f"check_breakers: {CASES.name} against {url}")
    return subprocess.call([sys.executable, "-m", "pytest", str(CASES), "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
