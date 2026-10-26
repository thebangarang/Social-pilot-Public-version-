"""Proving that a name is a name.

SQLite and Postgres both refuse a bound parameter where an IDENTIFIER belongs — you cannot write
`PRAGMA table_info(?)`, and `select * from ?` is a syntax error. The only correct treatment of a table or column
name is therefore to prove the value is one before it goes near a statement, which is what this does.

It lives in `app/` rather than in `scripts/` because the code that needs it is not only the maintenance
scripts: the usage meter builds an UPDATE from a table name too. It exists as ONE module because the two
maintenance scripts and the meter must not drift apart on what a valid identifier is.
"""
from __future__ import annotations

import re

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class NotAnIdentifier(ValueError):
    """Raised instead of either interpolating something unknown or silently skipping it."""


def safe_ident(name: str) -> str:
    """The identifier, or an exception: never a value that could end a quoted name and start a statement.

    `fullmatch`, not `match` with a `$` anchor: `$` also matches before a trailing newline, so `'users\\n'`
    passed and the "identifier" that came back was not one. A second reviewer found it by running it.
"""
    if not isinstance(name, str) or not IDENT.fullmatch(name):
        raise NotAnIdentifier(f"refusing an identifier that is not one: {name!r}")
    return name
