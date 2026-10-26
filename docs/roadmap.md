# docs/roadmap.md — Phases de build

## MVP

Le démo de bout en bout du pipeline Génération, avec la publication passant par
l'adaptateur `mock` tant que les apps TikTok/Instagram ne sont pas auditées.

### Phase 0 — Cadrage & spec ✅
Recherche, spec, architecture, décisions, scaffold, git.

### Phase 1 — Squelette + auth + shell (4 tabs)
- Config, DB, modèles de base.
- **Auth + 2FA + admin users** (réutiliser `auth-2fa-module` depuis command-center).
- Shell dashboard 4 tabs (Génération / Performance / Community manager / Settings),
  thème whitelabel (variables CSS), login + 2FA page.
- Tests auth + RBAC + protected-route 401.

### Phase 2 — Settings
- Connexion réseaux sociaux : boutons + callbacks OAuth (stub `mock` d'abord,
  réels ensuite), liste/révocation des comptes.
- Fournisseur LLM (ollama / openai_compatible / openrouter) + modèle par défaut.
- Budget & conso : limite mensuelle, suivi visuel, historique, top-up manual/auto.
- Thème (presets de couleurs), admin users, 2FA, changement de mot de passe.

### Phase 3 — Génération (le cœur)
- Chat (SSE) + import content (multi-formats + transcodage) + médiathèque.
- Branding asset kit (chargement, consultation, modification, default).
- Presets de direction artistique.
- Pipeline complet : propositions numérotées → ajustements → validation →
  génération média → scan compliance → previews simulées → double approbation →
  programmation (calendrier minute).
- Mémoire de marque + feedback log (apprentissage).

### Phase 4 — Moteur de publication
- `PlatformAdapter` interface + adaptateur `mock` complet ✅ (idempotent par clé).
- Scheduler asyncio ✅ — `publish_due()` toutes les 30s, état `publishing` (claim
  avant l'appel réseau), réconciliation des envois bloqués, retry ×3 puis échec,
  anti double-post par `idempotency_key`.
- Adaptateurs réels TikTok (Content Posting + Business) et Instagram (Instagram
  Login) — activés quand credentials/audit dispo.
- Liens d'accès rapide post-publication + lien monitoring ✅.

### Phase 5 — Performance ✅
- Polling insights + snapshots (métriques unifiées) ✅ — table `metric_snapshot`,
  adapter `mock` `insights()` (déterministe + croissance dans le temps), boucle
  de polling 10 min dans le scheduler.
- Dashboard global ✅ — KPIs agrégés, tendance (Chart.js), table par post,
  engagement rate, détail par post (modal), filtre plateforme, copy-URL.
- Dérivées ✅ — engagement rate, best-time (grille jour×heure), format gagnant,
  leaderboard, anomalies.

### Phase 6 — Community manager ✅
- Inbox commentaires ✅ (polling mock + dédup par remote id).
- Classification vert/orange/rouge ✅ (règles + LLM pour les cas ambigus, 1 appel par lot).
- Réponses en un clic ✅, batch ✅ (sélection multiple + envoi groupé).
- Like auto retiré (non conforme CGU — engagement automatisé interdit).
- ⏳ Répartition temporelle du batch (throttle) — à faire avec les vrais adaptateurs.

### Phase 7 — Durcissement (avant prod client)
- ✅ API de management multi-client (télémetrie token-auth, `/api/v1/management`,
  voir `docs/management-api.md`). Modèle retenu : **un VPS par client, région UE**.
- ✅ Rate limiting / lockout login (5 échecs/15 min) + purge sessions (horaire).
- ✅ Docker + reverse-proxy TLS + `secure` cookie — Dockerfile + compose + Caddy
  (auto-TLS + rate_limit via xcaddy) ; build + démarrage conteneur vérifiés en live.
- ✅ Checklist Tier 1 — secrets scan, pip-audit (0 CVE), bandit, second-agent
  review indépendante + correctifs (scrypt 2^17, 2FA throttling, timing login,
  upload streaming, LLM injection boundary…). Voir `SECURITY.md`.
- ⏳ Pen test manuel complet + vérif backup/restore — à faire avant le premier client.

## Post-MVP (v2+)

- Réseaux additionnels via nouveaux adaptateurs ✅ (mock) : TikTok, Instagram,
  Facebook, LinkedIn, X, YouTube, Threads, Pinterest, Google Business Profile,
  Bluesky — connect, sélection, preview, publish, insights derrière l'interface
  `PlatformAdapter`. Drivers réels ✅ : Bluesky (App Password — connect, publish,
  insights, comments), TikTok (OAuth — connect + publish vidéo via Content
  Posting API, SELF_ONLY, app soumise en review 09/2026), Instagram (OAuth — connect +
  publish via container flow + URL média publique signée HMAC ; vérifié en réel).
  Prochain : LinkedIn.
  Stratégie de connect : app centralisée (1 app auditée par plateforme, les
  clients se connectent via OAuth — pas de BYOK par client).
- Autopilot progressif (après entraînement + validation du taux de satisfaction).
- Reporting PDF export ✅ (rapport client bilingue FR/EN, `GET /performance/report`).
- Calendrier éditorial avancé ✅ (onglet Calendrier — grille mensuelle programmé/publié,
  navigation mois, détail au clic, FR/EN).
- Gestion multi-marques ✅ entité `Brand`, comptes sociaux rattachés, sélecteur de
  marque active (persisté), tag des posts (`Post.brand_id` à la validation) et
  scoping complet par marque : calendrier, performance, community, branding kit,
  média library (`MediaItem.brand_id`) et presets (`StylePreset.brand_id`).
  Mode strict ✅ : génération bloquée sans marque + compte connecté lié ; le
  sélecteur de réseaux ne propose que les plateformes avec compte éligible ;
  langue de génération = langue de l'interface.
- Landing page + privacy policy + terms ✅ servies sur socialpilot.aiturn.io
  (VPS Hostinger via Caddy, domaine vérifié) pour les audits plateforme.
- App déployée ✅ sur le VPS Hostinger (adresse retirée) — container `socialpilot-app`
  partageant le Caddy Mattermost ; app + OAuth sur app.socialpilot.aiturn.io.
  Compose prod : `docker-compose.prod.yml` (réseau `mattermost_default`).
- Passage SQLite → PostgreSQL si un client grossit.
