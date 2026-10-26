#!/usr/bin/env python3
"""Re-key the values social-pilot stores encrypted at rest, from one app secret key to another.

The app secret key (SP_SECRET_KEY) is not only a session-signing key: a Fernet key is derived from it and
the stored platform tokens and saved LLM API key are encrypted with that derived key. Rotating the key
without this step would leave those values unreadable — the account would look "connected" and fail on
every publish. This script is the step that makes the rotation safe.

It is called by the internal secret-rotation script, which supplies both values in the environment:

    AITURN_OLD_SP_SECRET_KEY   the value still in the .env when this runs
    AITURN_NEW_SP_SECRET_KEY   the value that has just been written

Neither value is printed. The output is counts and key fingerprints.

    python scripts/rekey_secrets.py [--db data/social-pilot.db] [--check]

Exit codes are part of the contract, because the caller rolls the key file back on them:

    0   nothing left to do: every stored value reads back with the new key
    1   (with --check only) something does not read back yet — nothing was changed
    2   nothing was changed: the keys were missing, the database was not there, or the backup could not be
        taken. The caller rolls the .env back, because the stored values are still under the old key.
    4   the re-key covered every target it could READ, and some targets were not covered: either absent
        from this database, or unreadable (a file that is not a database, a failure on the query). Like 3,
        anything that was re-keyed is committed, so the key must NOT be rolled back — but the rotation did
        not cover those columns and TARGETS needs checking against the schema.
    3   the re-key was COMMITTED and some values decrypt with neither key. A person must look at them;
        the old key must NOT be put back, or the values re-keyed by this run become unreadable. This
        was 1 until a second review pointed out that the caller reads 1 as "nothing was re-keyed" and
        rolls the key back — over rows this run had just re-keyed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sqlite3
import time

import sys
from pathlib import Path

# The maintenance scripts are run as files, so the project root is not on the path by default.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.idents import safe_ident  # noqa: E402  (after the path insert, on purpose)

APP_DIR = Path(__file__).resolve().parent.parent
# (table, id column, encrypted column) — every column the app encrypts with the derived key.
TARGETS = [
    ("connected_accounts", "id", "token_enc"),
    ("connected_accounts", "id", "refresh_token_enc"),
    ("llm_settings", "id", "api_key_enc"),
    ("operators", "id", "totp_secret"),
]


def fernet(key_value: str):
    from cryptography.fernet import Fernet

    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key_value.encode()).digest()))


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:10]



def backup_database(db: Path) -> Path:
    """A verified copy of the database, taken before anything is written.

    The re-key rewrites stored ciphertexts in place and commits them in one transaction; its own failure text
    tells the operator to restore the affected rows from a backup. A step that can make that necessary must be
    the step that takes the backup — a second reviewer pointed out that this one did not, and that the recovery
    instruction could therefore name a file nothing had created.

    sqlite3's backup API rather than a file copy: it is safe while the database is in use and it produces a
    consistent snapshot. The copy is verified before the first write — it opens, `integrity_check` says ok, and
    it is not empty — and a copy that fails verification is DELETED rather than left behind looking like one.
    """
    target = db.with_name(f"{db.stem}.pre-rekey-{int(time.time())}{db.suffix}")
    if target.exists():
        raise RuntimeError(f"refusing to overwrite an existing backup at {target}")
    source = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    destination = sqlite3.connect(target)
    try:
        with destination:
            source.backup(destination)
    except sqlite3.Error as exc:
        # The copy is the first thing that reads the database, so it is also the first thing that can discover
        # the database is unreadable. That is a refusal, not a traceback.
        destination.close()
        target.unlink(missing_ok=True)
        raise RuntimeError(f"the database could not be read for a backup ({exc})") from exc
    finally:
        source.close()
        destination.close()
    target.chmod(0o600)             # it holds the same values as the database it copies
    try:
        check = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            ok = check.execute("pragma integrity_check").fetchone()[0] == "ok"
        finally:
            check.close()
    except sqlite3.Error:
        ok = False
    if not ok or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise RuntimeError("the backup could not be verified, so nothing was written; "
                           "the copy was removed rather than left looking like a backup")
    return target

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(APP_DIR / "data" / "social-pilot.db"))
    ap.add_argument("--check", action="store_true",
                    help="only verify that every stored value reads back with the new key")
    args = ap.parse_args()

    new_key = os.environ.get("AITURN_NEW_SP_SECRET_KEY", "")
    old_key = os.environ.get("AITURN_OLD_SP_SECRET_KEY", "")
    if not new_key or not old_key:
        print("✗ AITURN_OLD_SP_SECRET_KEY and AITURN_NEW_SP_SECRET_KEY must both be set", file=sys.stderr)
        return 2
    db = Path(args.db)
    if not db.is_file():
        print(f"✗ no database at {db}", file=sys.stderr)
        return 2

    if not args.check:
        # Before the first write, not after the first failure.
        try:
            print(f"✓ backup: {backup_database(db)}")
        except (RuntimeError, OSError) as exc:
            print(f"✗ {exc} — nothing was written and no target was touched", file=sys.stderr)
            return 2
    old_f, new_f = fernet(old_key), fernet(new_key)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    moved = already = failed = missing = unreadable_targets = 0
    for table, id_col, column in TARGETS:
        try:
            table, id_col, column = safe_ident(table), safe_ident(id_col), safe_ident(column)
            # guard-ok: the three names are validated on the line above, and an identifier cannot be a bound
            # parameter — the only correct treatment is to prove the identifier is one, which safe_ident does.
            select_sql = (f'select "{id_col}" as id, "{column}" as v from "{table}" '
                          f'where "{column}" is not null')
            rows = con.execute(select_sql).fetchall()
        except sqlite3.Error as exc:
            # A target the database does not have is COUNTED — without this the run reported success whenever
            # every target was missing: nothing moved, nothing failed, and the summary said so in the reassuring
            # direction. But only THAT error counts as missing. A second reviewer pointed out that
            # `except sqlite3.Error` covers every failure a sqlite driver can raise, so "the file is not a
            # database" was being reported as "these four targets do not exist", which is a different and more
            # alarming claim than the truth.
            message = str(exc).lower()
            if "no such table" in message or "no such column" in message:
                missing += 1
                print(f"· {table}.{column}: not applicable ({exc})")
            else:
                unreadable_targets += 1
                print(f"✗ {table}.{column}: could not be read ({exc}) — this is NOT a missing target",
                      file=sys.stderr)
            continue
        for row in rows:
            cipher = row["v"]
            if not isinstance(cipher, str) or not cipher.startswith("gAAAAA"):
                continue
            if args.check:
                try:
                    new_f.decrypt(cipher.encode())
                except Exception:
                    failed += 1
                    print(f"✗ {table}.{column} id={str(row['id'])[:8]} does not read back with the new key")
                continue
            try:
                plain = old_f.decrypt(cipher.encode())
            except Exception:
                # Already re-keyed — running this twice must be harmless, which is what makes a retry
                # after a failed rotation safe.
                try:
                    new_f.decrypt(cipher.encode())
                    already += 1
                    continue
                except Exception:
                    failed += 1
                    print(f"✗ {table}.{column} id={str(row['id'])[:8]} decrypts with neither key — this "
                          f"value was encrypted with a third key and needs a person to look at it")
                    continue
            update_sql = (f'update "{table}" set "{column}" = ? where "{id_col}" = ?')  # guard-ok: see above
            con.execute(update_sql, (new_f.encrypt(plain).decode(), row["id"]))
            moved += 1
    if not args.check:
        con.commit()
    con.close()

    if args.check:
        # --check changes nothing, so a non-zero exit here is simply "not clean yet".
        tail = []
        if missing:
            tail.append(f"{missing} target(s) DO NOT EXIST and were never checked")
        if unreadable_targets:
            tail.append(f"{unreadable_targets} could not be read and were never checked")
        clean = not (failed or missing or unreadable_targets)
        # The tick belongs to a clean run. A reviewer pointed out that this line opened with "✓ every stored
        # value that exists reads back" while returning 1 — a sentence that is true of the values it READ and
        # reads as reassurance about the whole run.
        lead = (f"✓ every stored value that exists reads back with the new key ({failed} failure(s))" if clean
                else f"✗ {failed} value(s) do not read back with the new key")
        print(lead + (" · " + " · ".join(tail) if tail else ""))
        return 1 if (failed or missing or unreadable_targets) else 0
    if failed:
        # Exit 3, never 1. The commit above has already happened: the values this run re-keyed are now
        # encrypted under the new key. A caller that reads "non-zero" as "nothing was re-keyed" and puts
        # the old key back would strand them — the account would look connected and fail on every
        # publish. So 3 means "committed, and a person has to look at N values".
        print(f"✗ re-keyed {moved} stored value(s) from {fingerprint(old_key)} to {fingerprint(new_key)}"
              f" · {already} already on the new key · {failed} that decrypt with NEITHER key")
        print("  The re-key is COMMITTED. Do not put the old key back: these values would become")
        print("  unreadable. They were encrypted with a third key — restore those rows from backup and")
        print("  re-key them with the key that was in use when that backup was taken.")
        return 3
    print(f"✓ re-keyed {moved} stored value(s) from {fingerprint(old_key)} to {fingerprint(new_key)}"
          f" · {already} already on the new key · {failed} unreadable")
    if missing or unreadable_targets:
        # Exit 4, distinct from 0 and 3: the run did everything it could on the targets it could READ, and this
        # many were not covered. Reporting 0 was the failure mode — a rotation that covered half the columns and
        # printed "✓ re-keyed 0 stored value(s)" in the reassuring direction. The two causes are named apart
        # because they call for different actions: a missing target means TARGETS is out of step with the
        # schema, an unreadable one means the database itself needs looking at.
        said = []
        if missing:
            said.append(f"{missing} do not exist in this database")
        if unreadable_targets:
            said.append(f"{unreadable_targets} could not be read at all")
        print(f"✗ {len(said) and ' and '.join(said)} — those target(s) were NOT re-keyed; the rest of the run "
              f"is committed", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
