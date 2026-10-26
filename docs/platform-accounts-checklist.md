# Checklist — Comptes dummy + apps dev (Social Pilot)

Objectif : créer un compte « dummy mais réel » sur chaque plateforme pour brancher
et tester le tool en conditions réelles (mock d'abord, drivers OAuth ensuite).
Tout dans Bitwarden, dossier dédié.

> ⚠️ **Ne pas mettre « dummy » / « test » dans le handle ni l'email.** Meta/Instagram
> suspendent les comptes qui « ont l'air faux » : « dummy » dans le nom, compte
> fraîchement créé qui publie via l'API immédiatement, et connexions depuis plusieurs
> IP (VPS + maison) en peu de temps = flag automatique. Créer un compte plausible
> (vrai nom de business, email neutre, numéro mobile réel, compte Pro/Business lié
> à une Page Facebook) et le laisser « vieillir » quelques jours d'activité normale
> avant tout test API.

## Prérequis (en premier)

- [ ] Créer l'email dédié — un **Gmail** recommandé : il sert d'identité Google
      (YouTube + Google Business Profile) ET de login pour les autres plateformes.
      Ex : `socialpilot.test@gmail.com`
- [ ] Un numéro de téléphone joignable (vérifications Meta / TikTok / X / Pinterest).
- [ ] Préparer un « business » fictif mais réel pour Google Business Profile :
      nom + adresse physique réelle (vérification par carte postale).
- [ ] Dossier Bitwarden « Social Pilot — Dummy ».

## Naming Bitwarden (cohérent avec la convention)

- Login social : `Social Pilot — <Plateforme> (dummy)`
- Clés dev (secure note) : `Social Pilot — <Plateforme> App`

Ex : `Social Pilot — Facebook (dummy)`, `Social Pilot — Meta App`.

## Phase A — Comptes sociaux (maintenant)

Groupe Meta (1 compte FB fédère 3 réseaux)

- [ ] Facebook : compte personnel dummy → créer une **Page Facebook** (on publie
      sur la Page, pas le profil).
- [ ] Instagram : compte dummy → passer **Business/Creator** → lier à la Page FB.
- [ ] Threads : compte créé depuis Instagram (même identité).

Groupe Google (1 compte pour 2 réseaux)

- [ ] YouTube : créer une chaîne avec le compte Google dummy.
- [ ] Google Business Profile : créer la fiche (adresse réelle) → lancer la
      **vérification par carte postale tout de suite** (5-14 jours).

Indépendants

- [ ] TikTok : compte dummy (passer Business recommandé).
- [ ] LinkedIn : compte personnel dummy + une **Company Page**.
- [ ] X : compte dummy.
- [ ] Pinterest : compte **Business** dummy.
- [ ] Bluesky : compte dummy.

## Phase B — Apps dev + clés (quand on construit les drivers)

Meta (une app unique pour Facebook + Instagram + Threads)

- [ ] `developers.facebook.com` → créer 1 App « Business » → App ID + App Secret.
- [ ] Ajouter les produits : **Instagram Login** (`graph.instagram.com`), **Threads API**.
- [ ] Note : Threads API exige une app review (délai).

Google (un projet unique pour YouTube + GBP)

- [ ] `console.cloud.google.com` → projet → activer **YouTube Data API v3**.
- [ ] Activer **Business Profile API**.
- [ ] Créer un client OAuth 2.0 → client_id + client_secret.

Indépendants

- [ ] TikTok : `developers.tiktok.com` → app → client_key + client_secret.
      NOTE : publication API en **private viewing** tant que l'app n'est pas
      auditée (le vrai goulot TikTok).
- [ ] LinkedIn : `developer.linkedin.com` → app → produits **Share on LinkedIn** +
      **Sign in with LinkedIn** → client_id + secret.
- [ ] X : `developer.x.com` → projet + app → clés. NOTE : accès API **payant**
      (tiers Basic ~100 $/mois — à vérifier, les tarifs évoluent) + vérification compte.
- [ ] Pinterest : `developers.pinterest.com` → app → client_id + secret (API v5).
- [ ] Bluesky : **pas de dev portal**. Settings → App Passwords → créer un
      App Password (on utilise handle + app password).

## Ordre recommandé (temps / attente)

1. Email Gmail (base de tout).
2. Bluesky — immédiat (app password).
3. Meta (FB → Page → IG Business → Threads) — rapide, mais review Threads = jours.
4. Google — YouTube immédiat ; GBP = carte postale, à lancer tôt.
5. Pinterest — rapide.
6. TikTok — rapide, mais audit app = le goulot pour la publication réelle.
7. LinkedIn — rapide, review modérée.
8. X — dernier (payant + vérification).

## Pour brancher (rappel technique)

- Chaque app dev demande une redirect URI (callback) — on la fixera à
  `https://<domaine-client>/api/oauth/callback/<plateforme>` au moment du driver.
- Les clés dev (client_id/secret) = **secure notes Bitwarden** (jamais dans le
  code ni en clair). Les tokens OAuth seront chiffrés Fernet en base (comme la
  clé OpenRouter).
- Un seul email dummy suffit pour tout ; l'identité Google sert YouTube + GBP.
