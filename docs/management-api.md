# Management API & multi-client (whitelabel) deployment

## Why this exists

Social Pilot is single-tenant by design: **one instance = one client**, one DB,
one `.env`. To operate many client instances, we need one place that sees every
instance's health and activity **without ever touching client data**. That place
is the command center (the operator's command center), which polls each
instance's management API.

This API is the contract between Social Pilot and that future command center.
We build the API now, connect nothing yet.

## Deployment model (recommended)

**One small VPS per client, EU region, self-hosted.** Rationale:

- **Isolation.** One client's outage, misconfiguration, or breach never touches
  another — no data poisoning, no shared blast radius. Matches the single-tenant
  architecture already baked into the code (no `tenant_id` column anywhere).
- **RGPD.** Each client keeps their data on their own box, so there is no
  cross-client mixing to prove or protect. The client is the *responsable de
  traitement* (controller); you (the vendor) are the *sous-traitant* (processor)
  → sign an Art. 28 DPA per client. Clean separation by construction.
- **Cost.** A small EU VPS runs ~5–10 €/month. The client pays hosting (their
  instance, their budget, or billed through you). This scales linearly and
  predictably.

**Avoid** one shared server with per-client "remote access": shared infra means
shared DB risk, one breach hits everyone, and RGPD separation becomes hard to
demonstrate. If you later want density, move to **one container per client on a
few dedicated hosts** (still isolated, but you manage the hosts) — only after
the VPS-per-client model is proven.

## What the API exposes — and what it does not

- **Exposes (telemetry only):** instance id, label, version, uptime, DB health,
  and counters — operators, connected accounts, posts, publications,
  comments, and usage (aggregated cost/tokens per day and model, with the
  provenance of every price).
- **Never exposes:** any personal data, post/comment content, operator emails,
  client media, API keys. Telemetry-only keeps the command center RGPD-light:
  no personal data leaves the client instance.

## Auth

- Header: `X-Management-Token`.
- Constant-time comparison against `SP_MANAGEMENT_TOKEN`.
- Disabled (404) until `SP_MANAGEMENT_TOKEN` is set.
- Expose behind the reverse proxy with TLS and rate limiting (the app binds
  `127.0.0.1`; the proxy is the only public entry point).

## Endpoints

### `GET /api/v1/management/health`

```json
{
  "instance_id": "0f3b…",      // uuid5(secret_key) — stable per instance
  "label": "Client Name",
  "version": "0.1.0",
  "status": "ok",              // ok | degraded
  "db": "ok",
  "uptime_seconds": 12345,
  "timestamp": "2026-09-06T…"
}
```

### `GET /api/v1/management/stats`

```json
{
  "instance_id": "0f3b…",
  "label": "Client Name",
  "operators": 2,
  "connected_accounts": 3,
  "posts_total": 41,
  "publications_published": 12,
  "publications_scheduled": 2,
  "comments_new": 17,
  "comments_replied": 9,
  "usage_month_usd": 0.0042,
  "usage_all_time_usd": 0.0187,
  "tokens_all_time": 12345,
  "timestamp": "2026-09-06T…"
}
```

### `GET /api/v1/management/usage`

The billing-grade detail behind "consumption measured model by model, re-billed at
real cost". Aggregates only — no prompt, no output, no operator ever leaves.

Query params: `since` / `until` (`YYYY-MM-DD`, inclusive, default: current month),
`group_by` (default `day,model`; also accepts `provider`, `price_source`).

```json
{
  "instance_id": "0f3b…",
  "label": "Client Name",
  "since": "2026-09-01",
  "until": "2026-09-30",
  "currency": "USD",
  "total_cost_usd": 0.2509,
  "total_tokens": 1800,
  "calls": 3,
  "rows": [
    {
      "day": "2026-09-10", "provider": "openai_compatible", "model": "deepseek-flash",
      "tokens_in": 1000, "tokens_out": 500, "tokens_cached": 0,
      "cost_usd": 0.0009, "price_source": "pinned", "calls": 1
    }
  ],
  "unpriced_rows": 1
}
```

`price_source` is the provenance of the number: `provider` (the provider reported
its own cost — always wins), `pinned` (price from `app/model_prices.json`, dated
and sourced), `unknown` (**must not be billed** — add the model to the price map).
`unpriced_rows` counts the `unknown` ones: a statement must not be generated for a
period that contains any. Prices are pinned in the repo, never fetched at request
time; `scripts/usage_self_test.py` re-checks every call shape the instance uses
(fail = a shape is unpriced, inconclusive = provider unreachable).

## Provisioning a client instance

1. Provision the VPS (EU region), reverse proxy + TLS, `127.0.0.1` bind only.
2. `make setup` then `make admin` (create the client's admin user).
3. `python scripts/generate_management_token.py` → copy
   `SP_MANAGEMENT_TOKEN` + `SP_INSTANCE_LABEL` into `.env`.
4. Restart. The instance now answers `/api/v1/management/*`.

## Future command-center integration (not built yet)

**Pull model.** The command center polls each registered instance's `/health`
and `/stats` on an interval and keeps a registry of `instance_id → label,
status, usage`. Pulling (command center → instances) avoids opening inbound
access to client boxes — each instance only exposes its own telemetry to a
known token.

Optional later: instances **push** heartbeats (webhook) to the command center
for near-real-time alerts. Either way, no client data flows to the command
center — only counters and health.
