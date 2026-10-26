"""Circuit breakers — per dependency, not per call site (R-ENG-022).

Why this exists here. A platform that is down returns an error, and the next attempt is made as if it were
independent: the scheduled loop tries again at the schedule's pace, every attempt is logged as a fresh
failure, and a row can burn all of its attempts against a service that was never going to answer. Two
consequences, both real: the platform sees a retry storm from one client, and the app's own log stops
distinguishing "we have not retried yet" from "we retried and it is still down".

The state machine is the standard one and it is small on purpose:

    closed    --threshold consecutive failures-->  open
    open      --after the cooldown------------->   half-open (one trial)
    half-open --the trial succeeds------------->   closed
    half-open --the trial fails---------------->   open, cooldown doubled (capped)

Per dependency, because a failing TikTok must not stop Bluesky: the registry is keyed by the platform name.
In memory, because this app is one process and a restarted process has no useful memory of a dependency's
mood — that is a real limitation, written here instead of implied.

The clock is injectable (`now=`), because a breaker that can only be tested by sleeping is a breaker whose
cooldown has never been measured.
"""
from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass, field


def _env_float(name: str, default: float) -> float:
    """A positive, finite number from the environment, or the default.

    `float()` accepts more than a configuration can mean: `nan` makes every comparison false — so a cooldown
    of NaN never reports "still inside the cooldown" and the breaker hands out a call every time, reading as
    `half_open` forever — and `inf` makes `int()` raise at import. Both are worse than a wrong setting: one
    silently disables the breaker, the other stops the app from starting. A value this class cannot use is not
    a value it should accept.
    """
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or value <= 0:
        return default
    return value


THRESHOLD = int(_env_float("SP_BREAKER_THRESHOLD", 5))
COOLDOWN_SECONDS = _env_float("SP_BREAKER_COOLDOWN_SECONDS", 300)
MAX_COOLDOWN_SECONDS = _env_float("SP_BREAKER_MAX_COOLDOWN_SECONDS", 3600)


class DependencyHalted(RuntimeError):
    """Raised when a dependency's breaker is open: the caller must defer, not count a fresh failure.

    A distinct type for a distinct meaning — an open breaker says "we know this dependency is down", while an
    exception from the call itself says "this attempt failed". Folding the two together is how a row burns
    its retries against a service nobody has proved is back.
    """

    def __init__(self, name: str, state: dict):
        self.name, self.state = name, state
        super().__init__(f"{name} is halted ({state['state']}, retry in {state['retry_in']:.0f}s)")


@dataclass
class Breaker:
    """One dependency's state. One instance per dependency, shared by every caller of it."""

    name: str
    threshold: int = THRESHOLD
    cooldown: float = COOLDOWN_SECONDS
    max_cooldown: float = MAX_COOLDOWN_SECONDS
    failures: int = 0
    opened_at: float | None = None
    current_cooldown: float = 0.0
    trial_at: float | None = None
    trial_in_flight: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self, now: float | None = None) -> bool:
        """May this dependency be called right now? In half-open, exactly one caller gets a yes."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if self.opened_at is None:
                return True                                     # closed
            if now - self.opened_at < self.current_cooldown:
                return False                                    # open
            # The cooldown has passed: one trial. A trial that is never recorded — a crash mid-call, a
            # process that died — must not wedge the breaker shut forever, so a trial only blocks further
            # ones for its own cooldown window rather than for the life of the process.
            if self.trial_in_flight and self.trial_at is not None and now - self.trial_at < self.current_cooldown:
                return False
            self.trial_at, self.trial_in_flight = now, True
            return True

    def record(self, ok: bool, now: float | None = None) -> None:
        """Report the outcome of a call that `allow()` permitted."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self.trial_in_flight = False
            if ok:
                self.failures, self.opened_at, self.current_cooldown, self.trial_at = 0, None, 0.0, None
                return
            self.failures += 1
            if self.opened_at is None:
                if self.failures >= self.threshold:
                    self.opened_at, self.current_cooldown = now, self.cooldown
                return
            # A trial that failed re-opens with a longer cooldown: a dependency that failed again after being
            # given a chance is less likely to be back than one nobody has tried yet.
            base = self.current_cooldown or self.cooldown
            self.opened_at = now
            self.current_cooldown = min(base * 2, self.max_cooldown)
            self.trial_at = None

    def state(self, now: float | None = None) -> dict:
        """What this breaker is doing, for a log line or a management endpoint."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if self.opened_at is None:
                name = "closed"
                retry_in = 0.0
            elif now - self.opened_at < self.current_cooldown:
                name = "open"
                retry_in = self.current_cooldown - (now - self.opened_at)
            else:
                name = "half_open"
                retry_in = 0.0
            return {"name": self.name, "state": name, "consecutive_failures": self.failures,
                    "retry_in": round(max(retry_in, 0.0), 1),
                    "cooldown": round(self.current_cooldown, 1), "threshold": self.threshold}


_REGISTRY: dict[str, Breaker] = {}
_REGISTRY_LOCK = threading.Lock()


def breaker_for(name: str) -> Breaker:
    with _REGISTRY_LOCK:
        if name not in _REGISTRY:
            _REGISTRY[name] = Breaker(name=name)
        return _REGISTRY[name]


def allow(name: str) -> bool:
    return breaker_for(name).allow()


def record(name: str, ok: bool) -> None:
    breaker_for(name).record(ok)


def states() -> dict[str, dict]:
    with _REGISTRY_LOCK:
        return {name: b.state() for name, b in sorted(_REGISTRY.items())}


def reset() -> None:
    """Forget every breaker. Tests use it; a process should not, because a dependency's mood outlives one run."""
    with _REGISTRY_LOCK:
        _REGISTRY.clear()
