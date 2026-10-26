"""Call-shape coverage self-test — the guard against silent $0 metering.

The failure mode this exists for: a provider route records ``cost: 0 / spend: 0``
while the system looks healthy, so the ledger under-bills for months. It is not
hypothetical — LiteLLM shipped exactly that on the Anthropic ``/v1/messages`` and
passthrough routes (issue #24204, 53,725-token requests recorded at 0) and still
does not track the managed Batch API (issue #30635). Any ledger can grow the same
hole the day a project adds a call shape nobody re-tested.

Usage in a project (see the project's ``scripts/usage_self_test.py``):

    from usage_meter_self_test import Shape, run_self_test
    report = run_self_test([
        Shape("openrouter-chat", lambda: call_text("ping")),
        Shape("image", lambda: generate_image("a grey square"), expect="positive"),
        Shape("local-model", lambda: call_text_local("ping"), expect="free"),
    ], fetch_latest_row=my_fetch)
    print(report)   # exits non-zero through SystemExit if any shape failed

A shape that cannot reach its provider reports ``inconclusive`` — never ``pass``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("usage_self_test")


@dataclass
class Shape:
    """One call shape to exercise. ``call`` performs a minimal real request."""

    name: str
    call: object  # callable() -> usage dict | None
    expect: str = "positive"  # positive | free | any


@dataclass
class Result:
    name: str
    status: str  # pass | fail | inconclusive
    detail: str


def _check(shape: Shape, row) -> Result:
    if row is None:
        return Result(shape.name, "fail", "no ledger row was written for this call")
    source = getattr(row, "price_source", "unknown")
    tokens = getattr(row, "tokens", None) or 0
    cost = getattr(row, "cost", 0.0) or 0.0

    if not tokens:
        return Result(shape.name, "fail", "row written with 0 tokens (provider usage block not parsed?)")
    if source == "unknown":
        return Result(shape.name, "fail", f"price unknown for {row.provider}:{row.model} — add it to model_prices.json")
    if shape.expect == "positive" and cost <= 0:
        return Result(shape.name, "fail", f"cost is {cost} but this shape is expected to be billable")
    if shape.expect == "free" and cost > 0:
        return Result(shape.name, "fail", f"cost is {cost} but this shape is declared free (local inference?)")
    return Result(shape.name, "pass", f"tokens={tokens} cost={cost} source={source}")


def run_self_test(shapes, fetch_latest_row, *, disabled: bool = False) -> list[Result]:
    """Fire one request per shape and assert on the row it produced.

    ``fetch_latest_row()`` must return the newest ledger row (or None). It is
    called after each shape, so keep it cheap.
    """
    results: list[Result] = []
    if disabled:
        return [Result("all", "inconclusive", "self-test disabled by config")]

    if not shapes:
        return [Result("all", "fail", "no call shape configured — the self-test would prove nothing")]

    for shape in shapes:
        try:
            shape.call()
        except Exception as exc:  # noqa: BLE001 - any failure here is inconclusive, not a pass
            results.append(Result(shape.name, "inconclusive", f"could not run the call: {type(exc).__name__}: {exc}"))
            continue
        results.append(_check(shape, fetch_latest_row()))

    failed = [r for r in results if r.status == "fail"]
    if failed:
        for r in failed:
            log.error("usage self-test FAILED: %s — %s", r.name, r.detail)
    return results


def report_lines(results: list[Result]) -> list[str]:
    icon = {"pass": "OK  ", "fail": "FAIL", "inconclusive": "????"}
    return [f"{icon[r.status]}  {r.name}: {r.detail}" for r in results]


def exit_code(results: list[Result]) -> int:
    if any(r.status == "fail" for r in results):
        return 1
    if any(r.status == "inconclusive" for r in results):
        return 2
    return 0
