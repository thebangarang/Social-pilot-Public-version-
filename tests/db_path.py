"""Which SQLite file a test session may drop and recreate — validated, because that is a destructive act.

The session fixture runs `Base.metadata.drop_all()` against whatever this returns, so this function decides
what gets destroyed. Two rounds of review found two ways it could aim that at something real:

1. `TEST_DB_NAME=/etc/passwd` or `../../elsewhere/data/prod.db` — a name carrying a path left the project
   entirely. (Review of 915084f.)
2. `TEST_DB_NAME=social-pilot.db` — a plain name, inside `data/`, naming the APPLICATION'S LIVE DATABASE,
   which sits in the same directory. Containment inside `data/` was never the property that mattered: `data/`
   holds the test databases and the real one side by side. (Review of 155fd39.)

A third: `data/test-check.db` as a symlink — or a hard link — to the live database. It satisfies every rule
about the NAME while `drop_all()` follows it, because the code was validating a name and calling drop_all on a
file. (Review of 70c8b2e.) The rules are now applied to what the name resolves to, and a file with more than
one link is refused.

The rules are those failures turned around, and they are deliberately narrow: a plain file name, inside
the project's own `data/`, whose name begins with `test`. The last one is a naming convention the code
enforces — the honest way to say "this suite owns the databases it named itself". There is no list of
forbidden names to keep up to date, and the live database is excluded by construction.

So the name is VALIDATED rather than sanitised. A run that asks for a database this suite must not touch is a
run to stop, not to quietly redirect to the default.
"""
from __future__ import annotations

import stat
from pathlib import Path

#: The prefix every test database must carry. `data/social-pilot.db` is the live database.
OWNED_PREFIX = "test"


class UnsafeTestDatabase(ValueError):
    """The requested test database is not one this suite may drop."""


def ensure_private(path: Path) -> Path:
    """Create the file if needed and make it owner-only.

    A SQLite database in this project's `data/` holds real rows — the suite's own fixtures, which include
    operator emails and password hashes. Created by a run with a permissive umask it lands 0644, readable by
    every account on the machine. The audit fails on exactly that, and it did: three such files, one of them
    left by a review probe.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path


def session_db_url(project_root: Path, name: str | None = None) -> str:
    """The SQLAlchemy URL for this run's test database, refusing anything this suite does not own.

    Named `session_db_url` and not `test_..._url`: pytest collects any module-level name starting with `test_`,
    including one that was imported, so a helper with that prefix becomes a test that errors at setup.
    """
    candidate = (name or "").strip() or "test.db"
    # A plain file name: no directory component, no `..`, no absolute path, no hidden file. `Path().name`
    # collapses all of those in one comparison.
    if Path(candidate).name != candidate or candidate.startswith("."):
        raise UnsafeTestDatabase(
            f"TEST_DB_NAME must be a plain file name, got {candidate!r}. This value selects the SQLite file "
            f"the test session drops and recreates, so it may not carry a path."
        )
    if not candidate.startswith(OWNED_PREFIX):
        raise UnsafeTestDatabase(
            f"{candidate!r} is not a test database. This suite only drops files it named itself, and they all "
            f"begin with {OWNED_PREFIX!r} — the live database sits in the same directory."
        )
    data = (project_root / "data").resolve()
    path = (data / candidate).resolve()
    # The assertion that states the property directly, rather than implying it through the checks above.
    if path.parent != data:
        raise UnsafeTestDatabase(f"{path} is outside {data} — this suite only drops databases it owns.")
    # A NAME is not a FILE. `data/test-check.db` as a symlink to the live database satisfies every rule above —
    # the name is plain, it begins with `test`, and resolving it lands in the same directory — while `drop_all()`
    # follows the link. The review of 70c8b2e measured exactly that against a scratch layout of this one. So the
    # rule is applied to what the name RESOLVES to as well.
    if not path.name.startswith(OWNED_PREFIX):
        raise UnsafeTestDatabase(
            f"{candidate!r} resolves to {path.name!r}, which is not a test database. A symlink is not a way to "
            f"name a file this suite owns."
        )
    # And a link has more than one name: a hard link to the live database resolves to a `test-*.db` path while
    # sharing every byte with the file it must not touch (`samefile`, identical st_dev/st_ino). A test database
    # has nothing else pointing at it.
    if path.exists() and path.stat().st_nlink > 1:
        raise UnsafeTestDatabase(
            f"{candidate!r} has {path.stat().st_nlink} hard links — it shares its bytes with another file, so "
            f"dropping it drops that one too."
        )
    return f"sqlite:///{ensure_private(path)}"
