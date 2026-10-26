# docs/architecture.md — Architecture technique

## Vue d'ensemble

Une app FastAPI monolithique (un seul processus + worker asyncio) servie derrière
un reverse-proxy TLS. Une instance = un client = un seul tenant. SQLite (WAL) +
médiathèque locale. Aucune dépendance de build front (HTML + vanilla JS + SSE).

```
                         ┌─────────────────────────────────────────────┐
                         │  FastAPI app (uvicorn, 127.0.0.1)           │
  Browser ── TLS ── nginx│  ├─ api/        (REST, session-gated)       │
                         │  ├─ services/   (pipeline, budget, metrics) │
                         │  ├─ agents/     (LLM orchestration, sandbox)│
                         │  ├─ platforms/  (tiktok, instagram, mock)   │
                         │  ├─ scheduler/  (publish + polling asyncio) │
                         │  ├─ models/ db  (SQLite WAL)                │
                         │  └─ media/      (uploads, 0600)             │
                         └─────────────────────────────────────────────┘
                                     │
                       OpenRouter (text+image+video)
                       Ollama (local) / cloud privé / ComfyUI
                                     │
                              TikTok API     Instagram Login API
                              Business API   (graph.instagram.com)
```

## Composants

| Module | Rôle |
|---|---|
| `app/config.py` | Settings (pydantic-settings), env vars `SP_*`. |
| `app/database.py` | SQLAlchemy engine (SQLite WAL), session. |
| `app/models.py` | Modèles (voir ci-dessous). |
| `app/security.py` | scrypt + Fernet (réutilisé du module auth). |
| `app/auth.py` / `app/deps.py` | sessions, TOTP, `get_current_operator`, `require_admin`. |
| `app/api/*` | `auth`, `users`, `chat` (SSE), `pipeline`, `branding`, `media`, `performance`, `community`, `settings`, `connect` (OAuth), `budget`, `theme`. |
| `app/services/pipeline.py` | Machine à états du post (concept → validé → média → compliance → preview → approuvé → programmé → publié). |
| `app/services/budget.py` | Suivi conso LLM/média, limites, historique. |
| `app/services/metrics.py` | Polling insights + snapshot + calcul des dérivées. |
| `app/agents/` | Orchestrateur LLM sandboxé : prompts d'équipe, toolset interne, mémoire de marque, feedback. |
| `app/llm/` | Abstraction provider (ollama / openai_compatible / openrouter), budget + routing log. |
| `app/media/` | Ingestion, validation (magic bytes), transcodage (ffmpeg/Pillow), montage vidéo, génération via la couche LLM (OpenRouter image/vidéo) ou ComfyUI local. |
| `app/platforms/` | `PlatformAdapter` interface + `mock`, `tiktok`, `instagram`. |
| `app/scheduler.py` | Boucle asyncio : publier les posts `ready` échus, poller les insights, répartir les batchs CM, **sauvegarde automatique**. |
| `app/services/backup.py` | Sauvegarde planifiée (DB + médias + branding + réglages) + rotation + restauration. |
| `app/static/` | SPA : `index.html`, JS modules, CSS (variables thème). |

## Modèle de données (SQLite)

```
operators        (auth) id, email*, name, role, password_hash,
                 totp_secret(enc), totp_enabled, is_active, created_at, last_login_at
sessions         id, operator_id, token_hash*, expires_at, last_seen_at
chat_session     id, owner_id, title, created_at, updated_at, archived
chat_message     id, session_id, role(user|assistant|system), content,
                 media_refs(json), created_at

connected_account  id, platform(tiktok|instagram), handle, account_meta(json),
                   token_enc, refresh_token_enc, token_expires_at, is_active
brand_asset        id, kind(logo|color|font|visual|guideline|other), filename,
                   media_ref, mime, notes, created_at
style_preset       id, name, directives(text), is_builtin, created_at
media_item         id, kind(image|video|audio|doc), original_name, stored_path,
                   mime, width, height, duration_s, size_bytes, created_at

post_workflow      id, owner_id, status(enum), title, brief, created_at, updated_at
proposal           id, workflow_id, number, variant(json: text + media_prompts), created_at
post_version       id, workflow_id, number, content(json), media_refs(json), status
compliance_scan    id, workflow_id, verdict(pass|risk|fail), risks(json), at
publication        id, workflow_id, platform, account_id, caption, media_refs,
                   status(scheduled|ready|publishing|published|failed),
                   scheduled_at, published_at, remote_post_id, remote_url,
                   idempotency_key
metric_snapshot    (post_publication_id, platform, captured_at, metric_key, value)

comment            id, account_id, post_id(remote), author_name, author_id,
                   text, sentiment, level(green|orange|red), status(new|replied|
                   skipped|escalated), reply_draft, replied_at, remote_comment_id
batch_run          id, kind(reply|like), items(json), throttle_plan(json), status

theme              id, name, accent_hex, bg_hex, fg_hex, muted_hex, ... , is_active
llm_setting        id, provider, base_url, api_key_enc, model, monthly_limit,
                   topup_mode(manual|auto), created_at
usage_record       id, kind(llm|media), provider, model, tokens, cost, created_at
audit_log          id, operator_id, action, target, detail(json), created_at
brand_profile      id, tone, audience, guidelines, examples(json), updated_at
feedback_log       id, workflow_id, verdict(approved|adjusted|rejected), note,
                   created_at
```

> `api_key_enc` / `token_enc` : chiffrés Fernet au repos. `remote_*` : identifiants
> renvoyés par les plateformes. `idempotency_key` : évite le double-post.

## Machine à états du post (`post_workflow.status`)

```
draft → generating → proposed → adjusting → concept_validated
      → media_ready → compliance_ok → previewed → approved
      → scheduled → ready → publishing → published   (| failed,  retryable)
```

Chaque transition est un endpoint + une vérification de garde (jamais de saut
d'étape). La publication exige le statut `approved` **plus** une double
confirmation explicite (`POST /publications/{id}/confirm` ×2 ou un champ
`confirm_phrase`).

## Surface d'API (principale)

- `POST /auth/login`, `/auth/login/2fa`, `/auth/logout`, `GET /auth/me`,
  `POST /auth/password`, `/auth/2fa/*` — (réutilisé, cf. skill `auth-2fa-module`).
- `GET/POST /users`, `PATCH/DELETE /users/{id}`, `POST /users/{id}/reset-2fa` (admin).
- `POST /chat/stream` (SSE) — génération de propositions en streaming.
- `POST /workflows` (créer), `POST /workflows/{id}/proposals` (regénérer),
  `POST /proposals/{id}/adjust` (ajuster), `POST /proposals/{id}/validate`
  (valider le concept → génère les médias), `GET /workflows/{id}`.
- `POST /compliance/{id}/scan`, `POST /workflows/{id}/preview` (previews simulées),
  `POST /publications/{id}/approve` (double), `POST /publications/{id}/schedule`.
- `POST /media/upload`, `GET /media`, `GET /branding`, `POST /branding`, ...
- `GET /connect/tiktok`, `/connect/instagram` + callbacks ; `DELETE /accounts/{id}`.
- `GET/PUT /settings/llm`, `/settings/budget`, `GET /usage` (conso).
- `GET /performance/overview`, `/performance/posts/{id}`, `GET /performance/metrics`.
- `GET /community/comments`, `POST /community/comments/{id}/reply`,
  `POST /community/batch-reply`, `POST /community/poll`.
- `GET/PUT /theme`, `GET /themes`.

## Sécurité (rappel — voir SECURITY.md)

- Tous les endpoints de contrôle derrière session ; HTML → redirect `/login`.
- Tokens OAuth chiffrés ; `state` + PKCE ; callbacks en allowlist.
- Agent sandboxé (aucun shell/code) ; LLM output = data untrusted.
- Routing log sur chaque appel LLM (GDPR) ; médias local-first.
- Append-only audit log.

## Déploiement

- Dev : `uvicorn` reload sur 127.0.0.1. Prod : container (Dockerfile) + nginx/Caddy
  TLS + `SP_ENV=production` (cookie `secure`). Une instance par client VPS.
