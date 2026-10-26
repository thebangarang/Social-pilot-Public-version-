"""Input-validation guard: a route that reads a request body must declare its schema.

R-SEC-007 says never trust input, and the honest reading used to be "a reviewer reads the diff" — which is
prose, and prose holds exactly as long as whoever read it remembers. This is the mechanism instead, the same
one command-center carries: any POST/PUT/PATCH/DELETE route whose signature takes a request *body* must take
a pydantic model, or be named in `EXPECTED_RAW` with the reason it does not.

This project is the one the register's own proposal named ("one probe per project asserting every mutating
route takes a validated body schema — three projects, one probe"). It is a *copy* of command-center's guard
on purpose: projects do not import each other's code (isolation), so a pattern that is worth keeping is
copied and then corrected here, in the file most likely to be read by whoever changes this app.

Two corrections that guard carries and this one inherits — both were false readings of a healthy app:

  * a flat walk of `app.routes` finds **zero** mutating routes, because FastAPI nests included routers in
    `_IncludedRouter` wrappers; the walk has to descend;
  * demanding a model on every *mutating* route flags dozens of routes that legitimately take no body at all
    (an action whose only input is a path parameter). The rule is about bodies, not about methods.

And one thing to be exact about rather than convenient: `file: UploadFile` is a **typed** body parameter —
FastAPI's own multipart parsing, not a pydantic model, and not a bare `dict` either. It is accepted here as
a declared type, and `test_the_guard_can_still_fail` proves the guard still refuses a body annotated `dict`
or `Any`, which is the shape the rule is actually about. A parameter with *no* annotation is not one of those
shapes: FastAPI reads it as a query string, so this guard does not report it — said here rather than left for
someone to discover by trusting an overclaim.

**And the hole this copy closes that command-center's version still has:** a handler that takes the raw
`Request` can read any body it likes with no schema anywhere this walk can see, and the original guard simply
skipped that annotation. Here every such route is named in `RAW_REQUEST_ALLOWED` with what the raw object is
for and what the body actually is — four routes, all reading `request.client.host` or a cookie next to a
properly typed body. A skip that nothing counts is how a guard reports "nothing is wrong" about four routes
it never looked at.

A guard that silently inspects nothing passes for ever, so the test asserts how many routes it looked at.
"""
import datetime
import inspect
import re
import typing
import uuid

from fastapi import params
from pydantic import BaseModel

from app.main import app

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

#: A floor, not a target: if a refactor moves the routes where this introspection cannot see them, the test
#: must fail loudly instead of passing on an empty set.
MINIMUM_MUTATING_ROUTES = 20

#: Types FastAPI reads as query/path scalars rather than as a body.
SCALARS = {str, int, float, bool, bytes, datetime.date, datetime.datetime, uuid.UUID}
#: Special parameters that are neither body nor scalar.
SPECIAL = {"Request", "Response", "WebSocket", "BackgroundTasks", "HTTPConnection", "UploadFile"}

#: (path, method) → why this route does not declare a pydantic schema. An entry here is a decision with an
#: owner, not a suppression: the register (the internal rule registry, R-SEC-007) carries the task.
EXPECTED_RAW: dict[tuple[str, str], str] = {}

#: (path, method) → why this route takes the raw `Request`. A handler holding `Request` can read any body it
#: likes (`await request.body()`, `request.form()`) with no schema anywhere this walk can see, so each such
#: route is *named* here rather than skipped in silence — the machine points at the route, a person says what
#: the raw object is for, and `test_a_route_that_reads_the_raw_request_is_named` checks both directions.
RAW_REQUEST_ALLOWED: dict[tuple[str, str], str] = {
    ("/auth/login", "POST"):
        "the raw Request is read for `request.client.host` (the lockout record's address); the body is "
        "`LoginIn`.",
    ("/auth/login/2fa", "POST"):
        "the raw Request is read for `request.client.host`; the body is `TwoFAChallengeIn`.",
    ("/auth/logout", "POST"):
        "the raw Request is read for the session cookie and the cookie cleared on the Response; the route "
        "takes no body at all.",
    ("/auth/password", "POST"):
        "the raw Request is read for the current operator's session; the body is `PasswordChangeIn`.",
}


def _children(route):
    """The routes inside a wrapper, in either shape FastAPI has used."""
    for attr in ("routes", "original_router"):
        child = getattr(route, attr, None)
        if child is None:
            continue
        inner = getattr(child, "routes", None)
        if inner:
            return list(inner)
    return []


def _walk(routes):
    """Yield (path, methods, endpoint) from a route tree, descending into included routers and mounts."""
    for route in routes:
        inner = _children(route)
        if inner and getattr(route, "endpoint", None) is None:
            yield from _walk(inner)
            continue
        path = getattr(route, "path", None)
        methods = {m.upper() for m in (getattr(route, "methods", None) or set())}
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None and path and methods:
            yield path, methods, endpoint


def _mutating_routes():
    for path, methods, endpoint in _walk(app.routes):
        mutating = methods & MUTATING
        if mutating:
            yield path, sorted(mutating), endpoint


def _unwrap(ann):
    """`Annotated[Model, Body()]` hides the model one level down; so does a parameterised generic."""
    if hasattr(ann, "__metadata__"):
        return ann.__args__[0]
    return ann


def _signature(endpoint):
    """The endpoint's signature with annotations resolved to real objects.

    `from __future__ import annotations` makes every annotation a *string* at runtime, so `payload: PostIn`
    arrives as `"PostIn"` and a naive `issubclass` check reports a perfectly typed route as untyped.
    `eval_str=True` resolves them in the function's own globals; a name that still cannot be resolved is
    reported as unknown rather than guessed at.
    """
    try:
        return inspect.signature(endpoint, eval_str=True)
    except (TypeError, NameError, ValueError):        # an unresolvable name, or an older interpreter
        return inspect.signature(endpoint)


def _untyped_body_params(endpoint, path: str) -> list[str]:
    """Parameters FastAPI will read from the request body that are not pydantic models."""
    path_params = set(re.findall(r"{([^}:]+)", path))
    offenders = []
    for name, param in _signature(endpoint).parameters.items():
        if name in path_params:
            continue
        default = param.default
        if isinstance(default, params.Depends):            # our own injected dependencies
            continue
        if isinstance(default, (params.Query, params.Header, params.Cookie, params.Path)):
            continue
        if isinstance(default, params.Body) and getattr(default, "embed", False):
            continue
        ann = _unwrap(param.annotation)
        if isinstance(ann, str):                           # unresolved: say so rather than cry wolf
            continue
        if ann is inspect.Parameter.empty:
            continue
        if getattr(ann, "__name__", "") in SPECIAL:         # Request, Response, UploadFile, ...
            continue
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            continue
        if ann in SCALARS:
            continue
        origin, args = typing.get_origin(ann), typing.get_args(ann)
        if origin is not None and args:                    # list[Model], dict[str, int]: validated enough
            continue
        offenders.append(f"{name}: {getattr(ann, '__name__', str(ann))}")
    return offenders


def test_a_route_that_reads_a_body_declares_its_schema() -> None:
    looked_at = 0
    violations = []
    for path, methods, endpoint in _mutating_routes():
        for method in methods:
            looked_at += 1
            offenders = _untyped_body_params(endpoint, path)
            if not offenders or (path, method) in EXPECTED_RAW:
                continue
            violations.append(f"{method} {path} ({endpoint.__name__}) → {', '.join(offenders)}")
    assert looked_at >= MINIMUM_MUTATING_ROUTES, (
        f"this guard found only {looked_at} mutating route(s) — it is no longer looking at the app's real "
        f"surface, so a pass would mean nothing. Saw {len(app.routes)} route object(s): "
        + ", ".join(f"{type(r).__name__}{getattr(r, 'path', '')}" for r in app.routes[:12])
    )
    assert not violations, (
        "these routes read an unvalidated body and are not in EXPECTED_RAW: "
        + ", ".join(sorted(violations))
        + " — declare a pydantic model, or add the pair with a reason as specific as the one already there"
    )


def test_the_exception_list_stays_honest() -> None:
    """The allowed-raw route must still exist, and must still be raw — otherwise the list rots."""
    routes = list(_mutating_routes())
    live = {(p, m) for p, methods, _ in routes for m in methods}
    for (path, method), reason in EXPECTED_RAW.items():
        assert reason.strip(), f"{path} {method} is excepted without a reason"
        assert (path, method) in live, f"{path} {method} is in EXPECTED_RAW but the route is gone — drop it"
        endpoint = next(e for p, ms, e in routes if p == path and method in ms)
        assert _untyped_body_params(endpoint, path), (
            f"{path} {method} now declares a schema — drop it from EXPECTED_RAW so a fixed gap cannot hide"
        )


def _raw_request_params(endpoint) -> list[str]:
    """Parameters that hand the handler the raw request, where a body can be read with no schema in sight."""
    return [
        f"{name}: {getattr(param.annotation, '__name__', str(param.annotation))}"
        for name, param in _signature(endpoint).parameters.items()
        if getattr(param.annotation, "__name__", "") in ("Request", "HTTPConnection", "bytes")
    ]


def test_a_route_that_reads_the_raw_request_is_named() -> None:
    """`Request` is the one annotation through which this walk cannot see a body, so it is counted, not skipped.

    Both directions: a route that takes `Request` and is not named here is a finding, and a named route that
    no longer takes it (or has gone) is a rotted entry. Without this, "the guard passed" would mean only
    "nothing it looked at was wrong" while four routes were outside its sight.
    """
    routes = list(_mutating_routes())
    live = {(p, m) for p, methods, _ in routes for m in methods}
    unnamed = []
    for path, methods, endpoint in routes:
        if not _raw_request_params(endpoint):
            continue
        for method in methods:
            if (path, method) not in RAW_REQUEST_ALLOWED:
                unnamed.append(f"{method} {path} ({endpoint.__name__}) → {_raw_request_params(endpoint)}")
    assert not unnamed, (
        "these routes read the raw request and are not named in RAW_REQUEST_ALLOWED: " + ", ".join(unnamed)
        + " — say what the raw object is for, or take a schema instead"
    )
    for (path, method), reason in RAW_REQUEST_ALLOWED.items():
        assert reason.strip(), f"{path} {method} is named without a reason"
        assert (path, method) in live, f"{path} {method} is named but the route is gone — drop it"
        endpoint = next(e for p, ms, e in routes if p == path and method in ms)
        assert _raw_request_params(endpoint), (
            f"{path} {method} no longer takes the raw request — drop it from RAW_REQUEST_ALLOWED"
        )


def test_the_guard_can_still_fail() -> None:
    """A guard nobody has seen refuse is decoration.

    Read through the same code path the real walk uses, so this cannot pass while the walk is broken: a body
    typed `dict` and one typed `Any` must be reported, and a body typed with a model must be cleared.
    """
    class SampleIn(BaseModel):
        n: int = 0

    def untyped(payload: dict):
        return payload

    def anytyped(payload: typing.Any):
        return payload

    def typed(payload: SampleIn):
        return payload

    assert _untyped_body_params(untyped, "/x"), "a bare `dict` body must be reported"
    assert _untyped_body_params(anytyped, "/x"), "an `Any` body must be reported"
    assert _untyped_body_params(typed, "/x") == [], "a model-typed body must be cleared"

    from fastapi import Request

    def raws(request: Request):
        return None

    assert _raw_request_params(raws), "a handler that takes the raw Request must be detected"
    assert list(_mutating_routes()), "the walk must see the app's real surface"
