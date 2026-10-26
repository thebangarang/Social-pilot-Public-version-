# Deployment — one VPS per client (production)

Social Pilot is single-tenant: **one instance = one client = one VPS**. This is
the production deployment path (Docker + Caddy + auto-TLS). See
`docs/management-api.md` for the multi-client monitoring model and RGPD stance.

## Prerequisites

- A VPS in an EU region (OVH/Hetzner/…), Ubuntu 24.04+, with a public IP.
- A domain/subdomain pointing at the VPS (e.g. `client.example.com` → A record).
- Ports 80 + 443 open (for Caddy's HTTP-01 TLS challenge + HTTPS).
- Docker + the compose plugin installed.

## One-time setup

```bash
# 1. Install Docker + compose plugin (Ubuntu)
curl -fsSL https://get.docker.com | sh

# 2. Clone the app (or copy it) onto the VPS
git clone <votre-fork> /opt/social-pilot
cd /opt/social-pilot

# 3. Create the .env (secrets) — 0600, never committed
make env                      # generates a random SP_SECRET_KEY
# then fill: SP_ENV=production, SP_DATABASE_URL, LLM key, OAuth creds, etc.
chmod 600 .env

# 4. State directories — owned by the container user (uid 10001), private perms
mkdir -p data media backups
sudo chown -R 10001:10001 data media backups
chmod 700 data media backups

# 5. Build + start (SITE_ADDRESS = the client's domain → auto-TLS)
SITE_ADDRESS=client.example.com docker compose up -d --build
```

## First-run tasks (inside the app container)

```bash
# Create the client's admin user (interactive)
docker compose exec app python scripts/create_user.py --role admin --name "Client" --email admin@client.example.com

# Management token for the command center (add to .env, then restart)
docker compose exec app python scripts/generate_management_token.py
```

Then log in at `https://client.example.com`, connect the client's social
accounts, and set the LLM provider.

## Operations

```bash
docker compose logs -f app            # app logs
docker compose logs -f caddy          # proxy logs (TLS, rate-limit hits)
docker compose exec app python scripts/init_db.py   # (re)create schema (additive)
docker compose pull && docker compose up -d          # update images
```

- **Backups**: `data/` (SQLite + WAL), `media/`, `backups/` are bind-mounted on
  the host at `/opt/social-pilot/{data,media,backups}`. Back these up with the
  existing DR script (restic/B2 — see the internal backup standard).
- **Single worker**: the scheduler lives in-process. Never run more than one
  uvicorn worker or publications would be double-posted.
- **Rate limiting**: per-account lockout in the app + per-IP throttle on
  `/auth/login` at the proxy (Caddy `rate_limit`, built via `./caddy/Dockerfile`).
- **Secure cookie**: the session cookie gets the `Secure` flag automatically
  when `SP_ENV=production` (the compose file sets it).

## Local (non-TLS) smoke test

```bash
SITE_ADDRESS=localhost docker compose up -d --build
# Caddy serves plain HTTP on :80 for `localhost` (no TLS), app on the internal network.
curl -I http://localhost/health
```
