"""A name must be proven to be a name — found by a second reviewer running it, kept by running it.

`IDENT.match('users\\n')` was True while `IDENT.fullmatch('users\\n')` is False: `$` also matches before a
trailing newline, so the "identifier" the helper returned could end a quoted name and start a statement. The
case is listed here in the form the reviewer used — the value itself, not a paraphrase of it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app.idents import NotAnIdentifier, safe_ident  # noqa: E402

NOT_NAMES = ("users\n", "abc\nxyz", "us ers", "1users", 'users"', "", "users; drop table operators",
             "public.users", "users--")


@pytest.mark.parametrize("name", sorted(n for n in NOT_NAMES if n))
def test_a_name_that_is_not_one_is_refused(name):
    with pytest.raises(NotAnIdentifier):
        safe_ident(name)


def test_a_trailing_newline_is_refused():
    """The exact case the reviewer ran: `match` with a `$` anchor accepts it, `fullmatch` does not."""
    with pytest.raises(NotAnIdentifier):
        safe_ident("users\n")


@pytest.mark.parametrize("name", ["connected_accounts", "operators", "id", "token_enc", "a", "_x9"])
def test_a_real_identifier_comes_back_unchanged(name):
    assert safe_ident(name) == name


@pytest.mark.parametrize("value", [None, 7, b"users", ["users"]])
def test_a_non_string_is_refused(value):
    with pytest.raises(NotAnIdentifier):
        safe_ident(value)
