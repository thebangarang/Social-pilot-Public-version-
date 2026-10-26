"""Circuit breakers: the state machine, and the behaviour they exist to change (R-ENG-022).

Two halves, both needed. The unit cases pin the state machine on an injected clock — a breaker whose cooldown
has never been measured is a breaker nobody has tested, and sleeping in a test to reach a cooldown is how a
suite becomes slow enough that someone deletes it. The pipeline cases assert what the rule is actually about:
a dependency known to be down stops being called, and the row that waited for it does not pay for the wait.
"""
from __future__ import annotations

import datetime as dt
import os
import uuid

import pytest
from sqlalchemy import delete

from app import breaker
from app.auth import utcnow
from app.models import ChatSession, Post, Publication
from app.platforms import _ADAPTERS
from app.services.pipeline import publish_due


# Rows this module creates, removed at the end of each test: the suite shares one database file, and a
# publication left scheduled in the past shows up in another test's calendar (which asserts on every entry it
# finds) and gets published by another test's `publish_due()`. Measured: the calendar case failed on a row
# made here, with an empty caption.
_MADE: list = []


@pytest.fixture(autouse=True)
def clean_slate(db):
    breaker.reset()
    _MADE.clear()
    yield
    breaker.reset()
    for pub in list(_MADE):
        row = db.get(Publication, pub.id)     # a test may have removed its own row already
        if row is not None:
            db.delete(row)
    db.commit()
    _MADE.clear()


@pytest.fixture()
def post(make_operator, db):
    op = make_operator(f"breaker-{uuid.uuid4().hex[:8]}@socialpilot.local")
    session = ChatSession(owner_id=op.id, title="breakers")
    db.add(session)
    db.commit()
    p = Post(owner_id=op.id, session_id=session.id, status="media_ready", content={})
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _scheduled(db, post, platform: str) -> Publication:
    pub = Publication(post_id=post.id, platform=platform, caption="", media_ids=[], status="scheduled",
                      scheduled_at=utcnow() - dt.timedelta(minutes=1))
    db.add(pub)
    db.commit()
    db.refresh(pub)
    _MADE.append(pub)
    return pub


class Spy:
    """An adapter that records its calls, so "was the dependency called" is a fact, not an inference."""

    def __init__(self, remote_id: str = "remote-1"):
        self.remote_id = remote_id
        self.calls: list = []

    def publish(self, publication, account):
        self.calls.append(publication.id)
        return (self.remote_id, f"https://example.test/{self.remote_id}")


class Failing:
    def __init__(self):
        self.calls: list = []

    def publish(self, publication, account):
        self.calls.append(publication.id)
        raise RuntimeError("the platform is down")


# --- the state machine, on an injected clock ------------------------------------------------

def test_it_opens_only_after_the_threshold():
    b = breaker.Breaker("tiktok", threshold=3, cooldown=60)
    for failure in range(2):
        assert b.allow(now=failure) is True
        b.record(False, now=failure)
    assert b.state(now=2)["state"] == "closed", "two failures are below a threshold of three"
    assert b.allow(now=2) is True
    b.record(False, now=2)
    assert b.state(now=2)["state"] == "open"
    assert b.allow(now=2) is False, "and now it refuses"
    assert b.state(now=2)["consecutive_failures"] == 3


def test_a_success_closes_it_and_forgets_the_failures():
    b = breaker.Breaker("tiktok", threshold=1, cooldown=60)
    b.record(False, now=0)
    assert b.state(now=0)["state"] == "open"
    assert b.allow(now=61) is True                    # the trial
    b.record(True, now=61)
    assert b.state(now=61) == {**b.state(now=61), "state": "closed", "consecutive_failures": 0,
                               "retry_in": 0.0, "cooldown": 0.0}
    assert b.allow(now=61) is True


def test_it_half_opens_once_per_cooldown_window():
    b = breaker.Breaker("tiktok", threshold=1, cooldown=100)
    b.record(False, now=0)
    assert b.allow(now=50) is False, "still inside the cooldown"
    assert b.allow(now=101) is True, "the cooldown has passed: one trial"
    assert b.allow(now=102) is False, "a second caller does not get a second trial"
    b.record(False, now=103)
    assert b.allow(now=103) is False, "the failed trial re-opened it"


def test_a_failed_trial_doubles_the_cooldown_up_to_the_cap():
    b = breaker.Breaker("tiktok", threshold=1, cooldown=100, max_cooldown=250)
    b.record(False, now=0)
    assert b.state(now=0)["cooldown"] == 100
    assert b.allow(now=101) is True
    b.record(False, now=101)
    assert b.state(now=101)["cooldown"] == 200, "a dependency that failed twice gets a longer rest"
    assert b.allow(now=250) is False
    assert b.allow(now=302) is True
    b.record(False, now=302)
    assert b.state(now=302)["cooldown"] == 250, "capped, not unbounded"


def test_a_trial_that_is_never_recorded_does_not_wedge_it_shut():
    """A process that died mid-call must not leave the dependency halted forever."""
    b = breaker.Breaker("tiktok", threshold=1, cooldown=100)
    b.record(False, now=0)
    assert b.allow(now=101) is True                   # a trial is handed out…
    assert b.allow(now=150) is False                  # …and never reported
    assert b.allow(now=250) is True, "a later window hands out a new trial"


def test_dependencies_are_isolated():
    breaker.breaker_for("tiktok").threshold = 1
    breaker.record("tiktok", False)
    assert breaker.allow("tiktok") is False
    assert breaker.allow("bluesky") is True, "a failing TikTok must not stop Bluesky"
    # Asking about bluesky created its breaker: two dependencies, two states, neither one shared.
    assert set(breaker.states()) == {"tiktok", "bluesky"}


# --- the publish path: what the rule is for -------------------------------------------------

@pytest.fixture()
def alone(db):
    """Remove the pending rows this test did not make.

    The suite shares one database file and `publish_due()` has no way to scope itself: it publishes every due
    row, so one test's leftovers are another test's calls and the counts it asserts on include work it never
    asked for. They are REMOVED rather than relabelled — writing `published` on a row that was never published
    puts a fact in a shared database that never happened, and the review of b48f86c said so. A pending row
    left behind by a finished test is litter: nothing asserts on it once its own test has ended.
    """
    db.execute(delete(Publication).where(Publication.status.in_(("scheduled", "publishing"))))
    db.commit()


def test_an_open_breaker_defers_the_row_and_the_adapter_is_never_called(db, post, monkeypatch, alone):
    pub = _scheduled(db, post, "tiktok")
    spy = Spy()
    monkeypatch.setitem(_ADAPTERS, "tiktok", spy)
    breaker.breaker_for("tiktok").threshold = 1
    breaker.record("tiktok", False)

    result = publish_due()

    db.expire_all()
    db.refresh(pub)
    assert spy.calls == [], "the network call never happened"
    assert pub.status == "scheduled", "deferred, not failed"
    assert (pub.attempt_count or 0) == 0, "and the row did not pay for the wait"
    assert result == {"published": 0, "failed": 0, "halted": 1}


def test_a_healthy_platform_publishes_while_another_is_halted(db, post, monkeypatch, alone):
    tiktok = _scheduled(db, post, "tiktok")
    bluesky = _scheduled(db, post, "bluesky")
    monkeypatch.setitem(_ADAPTERS, "tiktok", Spy("t"))
    monkeypatch.setitem(_ADAPTERS, "bluesky", Spy("b"))
    breaker.breaker_for("tiktok").threshold = 1
    breaker.record("tiktok", False)

    result = publish_due()

    db.expire_all()
    db.refresh(tiktok)
    db.refresh(bluesky)
    assert bluesky.status == "published"
    assert tiktok.status == "scheduled"
    assert result == {"published": 1, "failed": 0, "halted": 1}


def test_repeated_failures_open_it_and_the_next_run_does_not_call(db, post, monkeypatch, alone):
    """The whole point: the attempt that would have been wasted against a known-down platform is not made."""
    pub = _scheduled(db, post, "tiktok")
    failing = Failing()
    monkeypatch.setitem(_ADAPTERS, "tiktok", failing)
    breaker.breaker_for("tiktok").threshold = 2

    publish_due()                                     # failure 1
    publish_due()                                     # failure 2 — opens it
    assert breaker.breaker_for("tiktok").state()["state"] == "open"

    publish_due()                                     # would have been the third attempt

    db.expire_all()
    db.refresh(pub)
    assert len(failing.calls) == 2, "the halted run made no call"
    assert (pub.attempt_count or 0) == 2, "and the halt is not counted as the row's failure"
    assert pub.status == "scheduled"


def test_a_success_resets_the_counter_so_a_flaky_platform_is_not_halted(db, post, monkeypatch, alone):
    """Failures are counted *in a row*: a success clears the count, so a platform that sometimes works is
    never halted.

    One fresh row per run. A row that failed stays due and the next run retries it — so a shared row would
    script one call per run into three, and the row itself would go terminal after three attempts long before
    a platform-level threshold of five could be reached.
    """
    outcomes = [False, False, False, False, True]
    calls = {"n": 0}

    class Flaky:
        def publish(self, publication, account):
            if calls["n"] >= len(outcomes):
                raise AssertionError("a call beyond the scripted outcomes: the run published more rows than "
                                     "this test made eligible")
            ok = outcomes[calls["n"]]
            calls["n"] += 1
            if not ok:
                raise RuntimeError("flaky")
            return ("remote", "https://example.test/remote")

    monkeypatch.setitem(_ADAPTERS, "tiktok", Flaky())
    breaker.breaker_for("tiktok").threshold = 5

    for expected_ok in outcomes:
        pub = _scheduled(db, post, "tiktok")
        publish_due()
        db.expire_all()
        db.refresh(pub)
        assert (pub.status == "published") is expected_ok
        db.delete(pub)
        db.commit()

    assert calls["n"] == len(outcomes), "every scripted call was made"
    state = breaker.breaker_for("tiktok").state()
    assert state["state"] == "closed", state
    assert state["consecutive_failures"] == 0, "the success cleared four failures"


def test_a_cancellation_is_not_a_dependency_failure(db, post, monkeypatch, alone):
    """An interrupt on OUR side must not halt a platform that did nothing wrong.

    `except BaseException` recorded KeyboardInterrupt / SystemExit / asyncio cancellation on the same counter
    as a refused connection — the review of b48f86c measured exactly that, and a threshold of one turned a
    Ctrl-C into an open circuit.
    """
    from app.services.pipeline import publish_publication

    pub = _scheduled(db, post, "tiktok")

    class Interrupted:
        def publish(self, publication, account):
            raise KeyboardInterrupt("the operator stopped the run")

    monkeypatch.setitem(_ADAPTERS, "tiktok", Interrupted())
    with pytest.raises(KeyboardInterrupt):
        publish_publication(db, pub)

    state = breaker.breaker_for("tiktok").state()
    assert state["state"] == "closed", state
    assert state["consecutive_failures"] == 0, "an interrupt is not a fact about the platform"


def test_a_nonsense_cooldown_falls_back_to_the_default():
    """`nan` disables the cooldown silently (every comparison with NaN is false), `inf`/`0` are equally unusable."""
    for nonsense in ("nan", "inf", "-inf", "0", "-5", "abc", ""):
        os.environ["SP_BREAKER_TEST_VALUE"] = nonsense
        assert breaker._env_float("SP_BREAKER_TEST_VALUE", 300.0) == 300.0, nonsense
    os.environ["SP_BREAKER_TEST_VALUE"] = "120"
    assert breaker._env_float("SP_BREAKER_TEST_VALUE", 300.0) == 120.0, "a usable value is used"
    del os.environ["SP_BREAKER_TEST_VALUE"]
