"""The test database name is validated — because the session fixture drops and recreates whatever it names.

Both hostile cases here came from a review that ran the function instead of reading it: a name carrying a path
(review of 915084f), and the application's own live database, which sits in `data/` beside the test ones and
passed every containment check (review of 155fd39).
"""
import os
import stat

import pytest

from tests.db_path import UnsafeTestDatabase, session_db_url


def test_a_plain_name_stays_inside_the_projects_data_directory(tmp_path):
    url = session_db_url(tmp_path, "test-check.db")
    assert url == f"sqlite:///{tmp_path / 'data' / 'test-check.db'}"
    assert session_db_url(tmp_path, None).endswith("/data/test.db")


def test_blank_means_the_default(tmp_path):
    """An empty value is "not set", not a hostile one — refusing it would break `TEST_DB_NAME=`."""
    assert session_db_url(tmp_path, "   ").endswith("/data/test.db")


def test_the_live_database_is_refused(tmp_path):
    """The name that started the second review: plain, inside `data/`, and the application's own database."""
    with pytest.raises(UnsafeTestDatabase, match="not a test database"):
        session_db_url(tmp_path, "social-pilot.db")


def test_the_file_it_names_is_owner_only(tmp_path):
    """These files hold operator rows; 0644 puts them in every account's reach, and the audit fails on it."""
    url = session_db_url(tmp_path, "test-perms.db")
    path = tmp_path / "data" / "test-perms.db"
    assert url.endswith(str(path))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("hostile", [
    "/etc/passwd",                      # an absolute path
    "../../elsewhere/data/other.db",    # out of the project entirely
    "sub/dir.db",                       # a directory component
    "..",                               # the parent itself
    ".hidden.db",                       # a hidden file
])
def test_a_name_that_leaves_the_data_directory_is_refused(tmp_path, hostile):
    """Refused, not redirected: a run asking for a database this suite must not touch has to stop."""
    with pytest.raises(UnsafeTestDatabase):
        session_db_url(tmp_path, hostile)


def test_a_symlink_to_another_file_is_refused(tmp_path):
    """A name is not a file: `test-check.db` -> the live database satisfied every rule about the name."""
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "social-pilot.db").write_bytes(b"the live database")
    (data / "test-check.db").symlink_to(data / "social-pilot.db")

    with pytest.raises(UnsafeTestDatabase, match="resolves to"):
        session_db_url(tmp_path, "test-check.db")


def test_a_hard_link_is_refused(tmp_path):
    """A hard link resolves to a `test-*.db` path while sharing every byte with the file it must not touch."""
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "social-pilot.db").write_bytes(b"the live database")
    os.link(data / "social-pilot.db", data / "test-shared.db")

    with pytest.raises(UnsafeTestDatabase, match="hard links"):
        session_db_url(tmp_path, "test-shared.db")
