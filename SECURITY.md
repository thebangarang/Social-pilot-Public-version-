# SECURITY.md — Social Pilot

**Tier 1 (Critical).** Rationale: user authentication + 2FA, OAuth tokens giving
publish/comment/insight access to a client's social accounts, and a public-facing
login surface. A breach = takeover of the client's brand accounts + PII of their
community. Every deliverable is secured as if audited by a cybersecurity firm.

## Assets & threat model

| Asset | Sensitivity | Controls |
|---|---|---|
| Platform OAuth tokens (TikTok/Instagram) | **Critical** | Fernet-encrypted at rest (key = sha256(SECRET_KEY)); never logged; per-account rotation; refresh lifecycle managed |
| User passwords | Critical | scrypt (n=2^14, r=8, p=1), constant-time compare |
| TOTP secrets (2FA) | Critical | Fernet-encrypted at rest, never plaintext |
| Session tokens | High | sha256-only at rest, HttpOnly + SameSite=Lax cookie, 7-day TTL |
| LLM / media API keys | High | `.env` only (0600), never in code/logs/DB |
| Brand assets / media uploads | High | `media/` 0600, served only behind auth, size/type validation |
| Comment data (PII of end users) | High | Local-first, LLM routing logged, redaction before any cloud call |
| Audit log | High | Append-only, no delete/update endpoint |

## Standing rules (from line one)

1. **No secrets in code or logs.** All credentials via env. Scan before every commit.
2. **Parameterized queries only.** SQLAlchemy Core/ORM — never string-built SQL.
3. **Input validation everywhere.** Uploads validated by magic bytes + extension +
   size caps; URLs validated; LLM output treated as untrusted data (never executed).
4. **Prompt-injection defense.** Comment text and user input are untrusted data,
   never instructions. The in-app agent is a sandboxed function-caller with NO
   shell, NO code, NO filesystem beyond the media library — see `docs/architecture.md`.
5. **Least privilege.** `operator` vs `admin` roles; admin-only user management.
   No blanket shell. Control API gated behind session; unauth → 401.
6. **Double approval before any publish.** No code path publishes without two
   explicit human confirmations. Scheduler can only fire posts already in
   `approved` state.

## Auth & 2FA (reused from `auth-2fa-module` skill)

- Login (email + password) + optional TOTP second step (Google Authenticator).
- scrypt password hashing, `hmac.compare_digest` verification.
- TOTP secret encrypted at rest with Fernet.
- Idempotent 2FA setup; HMAC-signed 5-min 2FA challenge (no `itsdangerous`).
- Sessions: `secrets.token_urlsafe(32)`, sha256 stored, HttpOnly + SameSite=Lax,
  `secure=True` in production. Password change invalidates other sessions.
- Lockout guards: can't delete/demote/deactivate self; can't remove last admin.
- Generic 401 on login failure (no user enumeration).

## OAuth token handling

- Tokens stored encrypted, per (platform, account). Refresh tokens managed.
- OAuth `state` param (CSRF) + PKCE where supported.
- Callback URLs pinned to a configured allowlist (no open redirect).
- Re-auth and revocation flow in Settings.

## Data & GDPR

- Single-tenant per instance; no cross-client data. `data/` and `media/` local,
  0600, project-local.
- Media/brand assets default **local-first**; cloud LLM only for text on explicit
  user selection. Every LLM call logs its routing decision (see
  the internal routing standard). Comment PII redacted before any cloud call.
- Backup discipline: git + versioned DB backups before any deploy (see
  the internal backup standard).

## Data classification

What the data is decides where it may travel — the class is not a label, it is the routing rule.

| Class | What | May live / travel |
|---|---|---|
| **Critical** | Platform OAuth tokens, user passwords, TOTP secrets, the Fernet/SECRET_KEY material | Encrypted at rest, `.env` only. Never in logs, never in git, never in a prompt, never in a screenshot, never in agent memory |
| **High** | Session tokens, LLM/media API keys, brand assets, uploaded media, comment text (a community's PII) | Local only by default. Comment text may reach a cloud LLM **only** after redaction and only on explicit user selection, and the routing decision is logged (the internal routing standard) |
| **Internal** | Post drafts, schedules, analytics, the audit log | Stays in the instance. Not sent anywhere by default |

The rule that follows: **a class never travels upward by convenience.** If a Critical value would make
a debugging session easier, it does not get logged — it gets rotated or it stays where it is.

## Incident response

The playbook for this project is `INCIDENT-RESPONSE.md` (required by R-CMP-008 for a Tier 1 project).
What is worth repeating here:

- **An incident is**: a leaked or misused OAuth token, a client's brand account taken over, the
  database or `media/` exposed, the instance's credentials or the host compromised, or unexplained
  publishing activity.
- **First hour**: stop the app (`docker stop socialpilot-app`, or the devnet's container) — never the
  machine; **preserve before repairing** — copy the database, the `media/` directory and the journal to
  a dated directory, because a repaired system has no evidence in it.
- **Contain it**: revoke the affected platform tokens at the provider, then rotate `SECRET_KEY` (which
  invalidates every session), then rotate the account passwords.
- **Tell the client** the same day, in plain language: what happened, what they must do (re-connect
  their accounts), what we have already done. The operator decides; nobody else speaks for this.
- **If personal data is involved**, the 72-hour notification clock to the CNIL starts on discovery;
  the operator is the one who notifies.
- After: an entry in `WORKLOG`/`worklog.md` naming the cause, and a check or a rule that would have
  caught it — an incident that changes nothing will repeat.

## Deploy gates (before any client instance goes live)

- ✅ Login rate-limiting / brute-force lockout (5 failures / 15 min, per email).
- ✅ Session purge job (hourly, in the scheduler loop).
- ✅ `secure` cookie (production) + TLS via reverse proxy (Docker + Caddy, auto-TLS).
- ✅ Dependency audit + CVE scan (pip-audit clean) + second-agent review.
- ✅ Secrets scan + `.gitignore` verified before every commit.
- ⏳ **Pen test (manual vectors)** — the plan is below; run it against a devnet instance and record both halves
  with `record-pentest.py` (a plan without an execution is a plan). Quarterly re-review.

## Penetration test plan (R-SEC-042) — the manual pass, before the first client

**Scope.** This instance's own HTTP surface: its auth and session handling, its OAuth token store, its
publishing path and its media pipeline. **Out of scope:** the host, the reverse proxy and the gateway in front of
it (those belong to `internal-gateway`'s own review), and the platforms we post to.

**The automated half runs continuously and is not repeated by hand:** `bandit` and `pip-audit` over every
requirement, with every finding read and adjudicated one by one in the internal dependency triage. What a
person adds is the five vectors below, attempted against a **devnet** instance (`socialpilot-devnet`, mock
tenants, no client data ever) and recorded whatever the result — including the vectors that found nothing, which
is the only way a later reader can tell the difference between "clean" and "not tried".

| Vector | Where to attempt it here | What a finding looks like |
|---|---|---|
| SQL injection | every field that reaches a query: account labels, post bodies, media filenames, the usage and insight filters | a 500, a row you should not see, or query text in the response |
| XSS | every text that renders in the portal: account names, captions, media alt text, error messages echoed back | a stored payload executing, or markup rendered unescaped |
| IDOR | the ids in `/accounts`, `/posts`, `/media`, `/insights`: take one id and ask for another tenant's object | a 200 carrying another tenant's object (the demo tenants exist for exactly this) |
| SSRF | every field that takes a URL: the LLM gateway endpoint, connector callbacks, fetch-by-URL media | a response from an address the app should not reach (`169.254.169.254`, `file://`, an internal host) |
| Command injection | any field reaching a subprocess: media processing, exporters | command output in a response, or a file nobody asked for |

**Record it** (both, dated, and the second one naming the vectors it covered):

    python3 record-pentest.py --project social-pilot --kind plan \
        --by "<your name>" --report projects/social-pilot/SECURITY.md
    python3 record-pentest.py --project social-pilot --kind execution \
        --by "<your name>" --vectors sql-injection,xss,idor,ssrf,command-injection \
        --note "<what you covered, and what you did not>"

Both expire in twelve months: a test of a system that has since changed answers a question nobody asked.


## Audit trail

- **2026-09-06** — Tier 1 review (this pass):
  - Secrets scan: clean (only a dummy `password123` in a test fixture).
  - `pip-audit`: no known vulnerabilities.
  - `bandit`: 1 HIGH (MD5 in `mock.py` — non-security, deterministic mock seeding) → fixed (sha256); 6 LOW (subprocess in `media.py`) → reviewed safe (list-args, no `shell=True`, server-generated paths).
  - **Fixed: file permissions** — `data/` + `media/` were 0775, DB files 0644 → now 0700 / 0600.
  - **Fixed: upload extension not validated** (`media.py`) — could allow path-traversal-ish filenames; now whitelisted against known ext sets + rejected otherwise.
  - **Fixed: image magic-byte check** (`media.py`) — SECURITY.md claimed it, code didn't do it; now PIL-decode validation rejects non-image bytes.
  - **Added: prompt-injection defense** in the agent system prompt (external content = data, never instructions).
  - **Second-agent (independent) review** — no critical/auth-bypass/SQLi/path-traversal found. Addressed: scrypt raised to OWASP N=2^17 (128 MiB, + maxmem + param clamp, env-aware fast cost in tests); login timing equalized (dummy scrypt for unknown accounts); 2FA step now throttled via the same lockout; `compare_digest` on bytes (malformed headers → 401 not 500); comment text delimited + system-guarded before the community LLM; upload streams with a hard cap (no full-body buffering); filename sanitized/truncated; dead last-admin guard fixed + tested; `/health` no longer leaks operator count / db kind.
  - Deferred (accepted for single-tenant, behind proxy): CSRF tokens (SameSite=Lax mitigates), session idle timeout, provider-error echo redaction, video/audio magic-byte validation.
- Last dep audit: 2026-09-06 (clean). Last pen test: not yet run (scheduled before first client).
- Open findings: 0 critical/high/medium.

See `docs/architecture.md` for component-level controls.
