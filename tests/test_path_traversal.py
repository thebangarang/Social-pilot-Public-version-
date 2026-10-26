"""R-SEC-041 — no route treats a hostile value as a path.

All tiers: *validate file paths, no directory traversal*. The registry's own note said nothing sent a
traversal at any route, and it was right: `bandit` does not look for this, the route guards refuse
*undeclared fields* rather than hostile values, and every file path this app builds comes from an id it
generated itself. So a test asserting a particular file route refuses `..` would have nothing to point at.

What this does instead is the honest version: it takes the app's **own published contract** (`/openapi.json`,
unioned with the route table) and sends a traversal payload into **every parameter of every read-only route**,
then asserts two things a leak cannot hide behind:

  * no response body contains the sentinel — a string that exists only in a file this test wrote OUTSIDE the
    project, so its appearance can only have come from the app following a path out of its own tree;
  * no route answers 5xx or raises to a hostile value: an unhandled exception on input is its own defect.

Write routes are **not** swept, and they are named rather than silently skipped: a payload posted into a body
field would create a record, and a test that pollutes the database to prove a point is a worse problem than the
one it looks for. `test_the_probe_would_catch_a_leak` is what makes this a test rather than a gesture: it
installs a route that *does* follow the path, proves the sweep flags it, and removes it again.

The logic here is deliberately duplicated per project rather than shared from the foundation: the isolation
framework forbids a project reaching for another project's files, and a test that cannot be read without three
others is a test nobody reads.

    or        .venv/bin/python -m pytest tests/test_path_traversal.py -q
    report    TRAVERSAL_REPORT=1 .venv/bin/python -m pytest tests/test_path_traversal.py -q -s
"""
from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest

SENTINEL = "AITURN-TRAVERSAL-SENTINEL-3f9c2b7d"
READ_ONLY = {"get", "head"}
REPORT = os.environ.get("TRAVERSAL_REPORT") == "1"


@pytest.fixture(scope="module")
def sentinel(tmp_path_factory) -> Path:
    """A file that lives outside this project, whose only interesting property is its contents."""
    d = tmp_path_factory.mktemp("not-the-projects-data")
    f = d / "sentinel.txt"
    f.write_text(SENTINEL + "\n")
    return f


def payloads(sentinel: Path) -> list[str]:
    """The three the register names — encoded traversal, absolute path, null byte — plus two neighbours."""
    absolute = str(sentinel)
    return [
        absolute.replace("/", "%2f"),          # a path that leaves the tree, encoded
        "../" * 8 + "etc/hostname",            # ..%2f, unencoded
        "%2e%2e%2f" * 4 + "etc%2fhostname",    # ..%2f, encoded
        absolute,                              # a plain absolute path
        absolute + "%00.png",                  # null byte, the truncation classic
    ]


def declared_routes(client) -> tuple[list[tuple[str, str, list[str]]], str]:
    """(method, path template, query parameter names) for every read-only route.

    Two sources, UNIONED, because a schema is a cache: FastAPI builds `/openapi.json` once, so a route added
    after that answers requests while being absent from the document. In command-center that is exactly what the
    enforceability test caught; the union is cheap and removes the class.
    """
    spec = None
    try:
        r = client.get("/openapi.json")
        if r.status_code == 200:
            spec = r.json()
    except Exception:  # noqa: BLE001 - an app may not publish a schema; the route table is the fallback
        spec = None
    out: list[tuple[str, str, list[str]]] = []
    seen: set[tuple[str, str]] = set()
    if spec and spec.get("paths"):
        for path, ops in spec["paths"].items():
            for method, op in ops.items():
                if method.lower() not in READ_ONLY:
                    continue
                query = [p["name"] for p in (op.get("parameters") or []) if p.get("in") == "query"]
                out.append((method.upper(), path, query))
                seen.add((method.upper(), path))
    from fastapi.routing import APIRoute  # noqa: PLC0415 - only needed for the route table

    for route in getattr(client.app, "routes", []):
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(READ_ONLY & {m.lower() for m in route.methods}):
            if (method.upper(), route.path) not in seen:
                out.append((method.upper(), route.path, []))
                seen.add((method.upper(), route.path))
    source = "the published schema, unioned with the route table" if spec else "the route table (no schema)"
    return out, source


def concrete(path_template: str, value: str) -> str:
    """Every `{param}` in the template becomes the payload, URL-encoded once (the client encodes again)."""
    encoded = value.replace("%", "%25")
    return "/" + "/".join(encoded if part.startswith("{") else part
                          for part in path_template.strip("/").split("/"))


def sweep(client, sentinel: Path) -> dict:
    """The whole probe: problems, notes, and the composition behind them.

    Each route is first asked with a BENIGN value, and the hostile request is judged against that baseline.
    Without it the probe reports the environment's own state as a finding: social-pilot answers 503 on eight
    routes in tests because the services they talk to are not there, and a 503 caused by an absent dependency is
    not a traversal. What the payload must not do is turn a non-5xx into a 5xx, raise where the route answered,
    or come back carrying the sentinel — and a route that 5xx's benignly is NAMED as such rather than quietly
    excused.
    """
    routes, source = declared_routes(client)
    problems: list[str] = []
    notes: list[str] = []
    requests, statuses = 0, {}
    benign = "1"
    baseline: dict[tuple[str, str], object] = {}
    for method, path_template, query in routes:
        url = concrete(path_template, benign)
        if query:
            url += "?" + "&".join(f"{q}={benign}" for q in query)
        try:
            baseline[(method, path_template)] = client.request(method, url).status_code
        except Exception as exc:  # noqa: BLE001 - recorded, then compared like any other answer
            baseline[(method, path_template)] = exc.__class__.__name__
    for method, path_template, query in routes:
        was = baseline[(method, path_template)]
        if isinstance(was, int) and was >= 500:
            notes.append(f"{method} {path_template} answers {was} without a payload too — the route's own state "
                         f"in this environment, not the payload's doing")
        for value in payloads(sentinel):
            url = concrete(path_template, value)
            if query:
                url += "?" + "&".join(f"{q}={value.replace('%', '%25')}" for q in query)
            try:
                resp = client.request(method, url)
            except Exception as exc:  # noqa: BLE001 - an app that raises on input has not refused it
                requests += 1
                statuses["raised"] = statuses.get("raised", 0) + 1
                if isinstance(was, str):
                    notes.append(f"{method} {path_template} raises without a payload too — environmental")
                else:
                    problems.append(f"{method} {url} raised {exc.__class__.__name__} ({exc}) where the same "
                                    f"route answered {was} to a benign value — unhandled input is its own defect")
                continue
            statuses[resp.status_code] = statuses.get(resp.status_code, 0) + 1
            requests += 1
            body = resp.text if resp.headers.get("content-type", "").startswith(("application/json", "text/")) \
                else ""
            if SENTINEL in body:
                problems.append(f"{method} {url} returned the sentinel — it followed a path out of the tree "
                                f"({resp.status_code})")
            if resp.status_code >= 500 and not (isinstance(was, int) and was >= 500):
                problems.append(f"{method} {url} answered {resp.status_code} to a hostile value where a benign "
                                f"one got {was} — the payload changed the outcome")
    return {"problems": problems, "notes": notes, "routes": len(routes), "requests": requests,
            "statuses": statuses, "source": source}


def test_no_route_accepts_a_traversal(client, sentinel):
    result = sweep(client, sentinel)
    assert result["routes"], "no read-only route was found — nothing was swept, which is not a pass"
    assert result["requests"] >= result["routes"], "every route must have been swept at least once"
    for note in result["notes"]:
        print(f"  ·  {note}")
        warnings.warn(f"R-SEC-041: {note}", stacklevel=1)  # visible in the run summary, not buried in captured output
    if REPORT:
        print(f"\n  R-SEC-041 sweep: {result['routes']} read-only route(s) from {result['source']}, "
              f"{result['requests']} request(s)")
        print(f"  statuses: {dict(sorted(result['statuses'].items()))}")
        print(f"  notes: {len(result['notes'])}")
    assert not result["problems"], "traversal is not refused:\n  " + "\n  ".join(result["problems"])


def test_the_probe_would_catch_a_leak(client, sentinel):
    """A probe that cannot fail is not a probe. This installs a route that DOES follow the path, and asserts the
    sweep flags it — then removes the route, so nothing else in the session sees it."""
    from fastapi.responses import PlainTextResponse  # noqa: PLC0415 - only this test needs it

    def vulnerable(leak: str) -> PlainTextResponse:  # noqa: ANN001 - the param name is what the route exposes
        return PlainTextResponse(Path(leak).read_text())

    client.app.add_api_route("/__traversal_probe__/{leak:path}", vulnerable, methods=["GET"])
    try:
        result = sweep(client, sentinel)
    finally:
        client.app.router.routes = [r for r in client.app.router.routes
                                    if getattr(r, "path", "") != "/__traversal_probe__/{leak:path}"]
    assert result["problems"], "the sweep found nothing on a route that serves any path handed to it — it cannot " \
                               "be trusted"
