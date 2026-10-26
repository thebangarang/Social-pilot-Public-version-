#!/usr/bin/env python3
"""Live call-shape self-test — proves every shape this instance uses is priced.

Fires one minimal real request per call shape the instance is configured for,
then checks the ledger row each one produced (app/usage_meter.py). This is the
guard against the silent-$0 failure mode: a call shape nobody re-tested is how a
ledger looks accurate while it under-bills for months.

Honesty rules:
  * a shape whose provider is NOT configured is printed as SKIP with the reason —
    it is never counted as a pass;
  * if no shape could be built at all, the run FAILS (it would prove nothing);
  * a shape that cannot reach its provider is `inconclusive` (exit 2), never pass.

Exit codes: 0 = every shape passed, 1 = at least one shape failed,
2 = inconclusive (provider unreachable).

Usage: .venv/bin/python scripts/usage_self_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # usage_meter_self_test.py

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.llm import chat_completion, image_generation, resolve_model  # noqa: E402
from app.models import LLMSetting, UsageRecord  # noqa: E402
from app.usage_meter import PriceMap  # noqa: E402
from usage_meter_self_test import Shape, exit_code, report_lines, run_self_test  # noqa: E402

PROMPT = "Réponds uniquement : pong"


def _configured_setting() -> LLMSetting | None:
    db = SessionLocal()
    try:
        return db.scalar(select(LLMSetting).limit(1))
    finally:
        db.close()


def _latest_row() -> UsageRecord | None:
    db = SessionLocal()
    try:
        return db.scalar(select(UsageRecord).order_by(UsageRecord.id.desc()).limit(1))
    finally:
        db.close()


def build_shapes(setting: LLMSetting | None) -> tuple[list[Shape], list[str]]:
    """One Shape per call shape this instance is configured for, plus skip notes.

    The instance has a single active provider (llm_settings singleton), so the
    other providers' shapes are reported as skipped rather than faked.
    """
    provider = (setting.provider or "none").lower() if setting else "none"
    shapes: list[Shape] = []
    skipped: list[str] = []

    def chat(name: str, want: str, expect: str = "positive") -> None:
        if provider != want:
            skipped.append(f"{name}: provider '{want}' is not configured (active: '{provider}')")
            return
        shapes.append(
            Shape(name, lambda: chat_completion([{"role": "user", "content": PROMPT}]), expect=expect)
        )

    chat("openrouter-chat", "openrouter")
    chat("openai_compatible-chat", "openai_compatible")
    # Local inference: no marginal token cost, but the tokens are still counted.
    chat("ollama-chat", "ollama", expect="free")

    if provider == "openrouter":
        shapes.append(
            Shape("image", lambda: image_generation("a plain grey square on a white background"))
        )
    else:
        skipped.append(f"image: the /images endpoint is OpenRouter-native (active: '{provider}')")

    return shapes, skipped


def main() -> int:
    setting = _configured_setting()
    prices = PriceMap.load()
    shapes, skipped = build_shapes(setting)

    print(f"usage self-test — provider={setting.provider if setting else 'none'} "
          f"model={resolve_model(setting, 'text') if setting else '-'} "
          f"image={resolve_model(setting, 'image') if setting else '-'} "
          f"prices={prices.version}")
    for note in skipped:
        print(f"SKIP  {note}")
    if not shapes:
        print("FAIL  no call shape could be built — this run would prove nothing "
              "(configure a provider in Settings first).")
        return 1

    results = run_self_test(shapes, _latest_row)
    for line in report_lines(results):
        print(line)

    code = exit_code(results)
    print({0: "PASS", 1: "FAIL", 2: "INCONCLUSIVE"}[code])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
