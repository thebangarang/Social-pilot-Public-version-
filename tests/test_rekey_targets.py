"""The rotation must not report success when it covered nothing.

A second reviewer found this: every target the live database does not have raised `sqlite3.OperationalError`, was
printed as "not applicable", and was not counted — so a run that touched nothing fell through to
"✓ re-keyed 0 stored value(s)" and exited 0. A rotation that silently did nothing is worse than one that failed,
because the operator's next step depends on believing it.

The test runs the real script against an EMPTY database, where every target is missing, and asserts the exit
code. That is the case that was broken, reproduced without a database to damage.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT / "scripts" / "rekey_secrets.py"


def _two_keys() -> tuple[str, str]:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode(), Fernet.generate_key().decode()


def test_a_database_with_no_targets_does_not_report_success(tmp_path):
    pytest.importorskip("cryptography")
    import sqlite3
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    old_key, new_key = _two_keys()
    env = {"PATH": "/usr/bin:/bin", "AITURN_OLD_SP_SECRET_KEY": old_key, "AITURN_NEW_SP_SECRET_KEY": new_key}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--db", str(empty)],
                          capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 4, f"expected the missing-targets code, got {proc.returncode}: {proc.stdout}"
    assert "do not exist" in (proc.stdout + proc.stderr)
    # The reassuring line is still printed — it is true about the targets that DID exist — and it is no longer
    # the whole story, which is what made the bug invisible.
    assert "re-keyed 0 stored value(s)" in proc.stdout


def test_check_mode_is_not_clean_when_targets_are_missing(tmp_path):
    pytest.importorskip("cryptography")
    import sqlite3
    empty = tmp_path / "empty2.db"
    sqlite3.connect(empty).close()
    old_key, new_key = _two_keys()
    env = {"PATH": "/usr/bin:/bin", "AITURN_OLD_SP_SECRET_KEY": old_key, "AITURN_NEW_SP_SECRET_KEY": new_key}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--db", str(empty), "--check"],
                          capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 1
    assert "DO NOT EXIST" in proc.stdout


def test_a_file_that_is_not_a_database_is_refused_in_a_sentence(tmp_path):
    """Finding 1, after the fix for finding 4 moved the earliest failure to the backup.

    A file that is not a database used to surface as whichever driver error the target loop happened to hit, and
    `except sqlite3.Error` reported every one of them as "these targets do not exist". Now the copy reads the
    database before anything else, so this is discovered as a refusal: exit 2, one sentence naming the cause, and
    no traceback for an operator to decode. Nothing is written and no target is touched.
    """
    pytest.importorskip("cryptography")
    not_a_db = tmp_path / "notes.txt"
    not_a_db.write_text("this is not a database\n")
    before = not_a_db.read_bytes()
    old_key, new_key = _two_keys()
    env = {"PATH": "/usr/bin:/bin", "AITURN_OLD_SP_SECRET_KEY": old_key, "AITURN_NEW_SP_SECRET_KEY": new_key}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--db", str(not_a_db)],
                          capture_output=True, text=True, env=env, timeout=60)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 2, f"expected a refusal (2), got {proc.returncode}: {combined}"
    assert "nothing was written and no target was touched" in combined
    assert "Traceback" not in combined, "an operator gets a sentence, not a stack trace"
    assert not_a_db.read_bytes() == before, "the file must be untouched"
    assert list(tmp_path.glob("*.pre-rekey-*")) == [], "a failed copy must not be left behind"


