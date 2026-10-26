"""Usage metering — pinned, auditable cost accounting for one project.

Vendored copy: every project that calls a paid model carries its own copy of this
file plus ``model_prices.json``. It never imports from another project.

Why this exists: the pricing page promises that consumption is measured model by
model and re-billed at real cost, with no markup. That promise is only true if
every call lands in a ledger that can *prove* where its number came from.

The rule that matters: **never silently cost 0**. A row is either priced by the
provider, priced from the pinned map, or marked ``unknown`` and logged loudly.
A silent zero is how a system looks accurate while under-billing for months.

Token extraction and pricing are pure functions (no DB, no network) so they can be
tested without a provider.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from app.idents import safe_ident

log = logging.getLogger("usage_meter")

PRICES_PATH = Path(__file__).with_name("model_prices.json")
PRICE_SOURCES = ("provider", "pinned", "unknown")
#: Provenance values that cannot be billed: NULL (pre-meter rows) and unknown.
UNPRICED = (None, "unknown")


# --------------------------------------------------------------------------- #
# Prices
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PriceMap:
    models: dict = field(default_factory=dict)
    rule: dict = field(default_factory=dict)
    version: str = ""

    @classmethod
    def load(cls, path: Path | str | None = None) -> "PriceMap":
        raw = json.loads(Path(path or PRICES_PATH).read_text(encoding="utf-8"))
        return cls(models=raw.get("models") or {}, rule=raw.get("off_peak_rule") or {}, version=raw.get("version", ""))

    def lookup(self, provider: str, model: str) -> tuple[dict, str] | tuple[None, None]:
        """Exact ``provider:model`` wins, then ``provider:*``, then ``*:model``."""
        keys = (
            f"{(provider or '').lower()}:{(model or '').lower()}",
            f"{(provider or '').lower()}:*",
            f"*:{(model or '').lower()}",
        )
        for key in keys:
            if key in self.models:
                entry = self.models[key]
                alias = entry.get("_alias_of")
                if alias and alias in self.models:
                    return self.models[alias], alias
                return entry, key
        return None, None

    def is_off_peak(self, at: datetime) -> bool:
        """Off-peak when the request is outside the provider's peak windows (UTC)."""
        rule = self.rule or {}
        hours = rule.get("peak_hours_utc") or []
        days = rule.get("peak_weekdays_utc") or [0, 1, 2, 3, 4]
        at = at.astimezone(timezone.utc)
        if at.weekday() not in days:
            return True
        for start, end in hours:
            if start <= at.hour < end:
                return False
        return True


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Tokens:
    input: int = 0
    output: int = 0
    cached: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output

    @property
    def billable_input(self) -> int:
        """Input tokens billed at the full rate: cached ones are billed separately."""
        return max(self.input - self.cached, 0)


def extract_tokens(usage: dict | None) -> Tokens:
    """Read a provider ``usage`` block into a Token split.

    Handles the shapes actually seen in the wild:
    OpenAI/OpenRouter ``prompt_tokens`` / ``completion_tokens`` /
    ``prompt_tokens_details.cached_tokens`` and DeepSeek's
    ``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``.
    When a provider does not split cached from uncached, cached stays 0 and the
    whole input is billed at the uncached rate — never the other way round, which
    would under-bill.
    """
    usage = usage or {}
    try:
        total = int(usage.get("total_tokens") or 0)
        prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)

        detail = usage.get("prompt_tokens_details") or {}
        cached = int(detail.get("cached_tokens") or usage.get("cache_read_input_tokens") or 0)
        hit = usage.get("prompt_cache_hit_tokens")
        miss = usage.get("prompt_cache_miss_tokens")
        if hit is not None or miss is not None:
            cached = int(hit or 0)
            prompt = int(hit or 0) + int(miss or 0)
            completion = completion or 0

        if not prompt and not completion and total:
            prompt = total
        return Tokens(input=prompt, output=completion, cached=min(cached, prompt))
    except (TypeError, ValueError):
        return Tokens()


# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Priced:
    cost: float
    currency: str
    source: str  # provider | pinned | unknown
    note: str = ""

    @property
    def billable(self) -> bool:
        return self.source != "unknown"


def price_call(
    provider: str,
    model: str,
    tokens: Tokens,
    *,
    at: datetime | None = None,
    provider_cost: float | None = None,
    prices: PriceMap | None = None,
) -> Priced:
    """Price one call. The provider's own number always wins.

    ``provider_cost`` must be ``None`` when the provider did not report a cost —
    passing ``0.0`` would claim "this call was free", which is a different
    statement from "we do not know".
    """
    if provider_cost is not None:
        return Priced(cost=round(float(provider_cost), 8), currency="USD", source="provider")

    prices = prices or PriceMap.load()
    entry, key = prices.lookup(provider, model)
    if entry is None:
        log.warning(
            "usage_meter: NO PRICE for %s:%s (%s tokens) — row will be marked 'unknown'. "
            "Add it to model_prices.json (dated, from the provider's official page) before billing.",
            provider, model, tokens.total,
        )
        return Priced(cost=0.0, currency="USD", source="unknown", note=f"no price for {provider}:{model}")

    currency = entry.get("currency", "USD")
    if "flat" in entry:
        return Priced(cost=float(entry["flat"]), currency=currency, source="pinned", note=f"flat ({key})")

    # A provider with no off-peak concept carries only "peak" rates. Say so in the
    # note rather than labelling every row "peak", which would read as "we checked".
    bands = {
        band_name
        for field_name in ("input_cached", "input_uncached", "output")
        for band_name in (entry.get(field_name) or {})
    }
    has_off_peak = "off_peak" in bands
    off_peak = has_off_peak and prices.is_off_peak(at or datetime.now(timezone.utc))
    band = "off_peak" if off_peak else "peak"
    per_m = 1_000_000.0

    def rate(field_name: str) -> float:
        table = entry.get(field_name) or {}
        value = table.get(band)
        if value is None:
            value = table.get("peak", 0.0)
        return float(value)

    cost = (
        tokens.billable_input * rate("input_uncached")
        + tokens.cached * rate("input_cached")
        + tokens.output * rate("output")
    ) / per_m
    note = f"{key} {'off_peak' if off_peak else ('peak' if has_off_peak else 'flat')}"
    if not tokens.cached and tokens.input and "input_cached" in entry:
        note += " (no cache split reported by provider: input billed uncached)"
    return Priced(cost=round(cost, 8), currency=currency, source="pinned", note=note)


# --------------------------------------------------------------------------- #
# Ledger
# --------------------------------------------------------------------------- #
def record_usage(
    db,
    record_cls,
    *,
    kind: str,
    provider: str,
    model: str | None,
    usage: dict | None = None,
    provider_cost: float | None = None,
    at: datetime | None = None,
    prices: PriceMap | None = None,
    commit: bool = True,
):
    """Write one ledger row and return it.

    Sets the split columns and ``price_source`` when the model has them; a project
    that has not migrated yet still records (cost + tokens, as before), it simply
    loses the audit trail until the migration runs.
    """
    tokens = extract_tokens(usage)
    priced = price_call(provider, model or "", tokens, at=at, provider_cost=provider_cost, prices=prices)

    row = record_cls(kind=kind, provider=provider, model=model, cost=priced.cost)
    optional = {
        "tokens": tokens.total or None,
        "tokens_in": tokens.input,
        "tokens_out": tokens.output,
        "tokens_cached": tokens.cached,
        "price_source": priced.source,
        "price_note": priced.note[:200],
        "currency": priced.currency,
    }
    for name, value in optional.items():
        if hasattr(record_cls, name):
            setattr(row, name, value)

    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    return row


def unknown_price_rows(rows) -> list:
    """Rows that cannot be billed yet. A statement must refuse to be generated.

    A row written before the meter existed carries no provenance at all
    (``price_source`` NULL): we cannot tell a provider-reported figure from a
    silent zero, so it counts as unpriced until the migration backfills it —
    ``cost > 0`` → ``provider`` (the old code read the provider's field),
    ``cost = 0`` → ``unknown`` (unknown is the honest label).
    """
    return [r for r in rows if getattr(r, "price_source", None) in UNPRICED]




def legacy_backfill_statements(table: str = "usage_records") -> tuple[str, str]:
    """The two UPDATEs a project runs once, at migration time. Oldest rows first.

    Deliberately explicit rather than clever: a wrong backfill silently converts
    unpriced history into billable history, which is exactly the failure this
    component exists to prevent.

    The table name is interpolated — it cannot be a bound parameter — so it is proven to be an identifier
    first. A second reviewer found this site: the guard existed in `scripts/` and the application could not
    import it, which is why the module now lives in `app/`.
    """
    table = safe_ident(table)
    # guard-ok: the table name is proven to be an identifier on the line above. The statement is RETURNED
    # rather than executed here, so the injection check cannot follow it to the call that runs it — which is
    # why the marker is here and the blind spot is named in the rule register's debt. A reviewer measured the
    # marker against the check itself: written one line higher it describes a proof that had not happened yet.
    return (
        f"UPDATE {table} SET price_source = 'provider' WHERE price_source IS NULL AND cost > 0",
        f"UPDATE {table} SET price_source = 'unknown' WHERE price_source IS NULL AND cost = 0",
    )
