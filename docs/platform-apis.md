# docs/platform-apis.md — Contraintes réelles des APIs TikTok & Instagram

Synthèse de recherche (sept. 2026) sur ce que les APIs officielles permettent
réellement — et ce qu'elles interdisent. C'est ce document qui délimite le
périmètre **réel** des tabs Génération / Community manager / Performance.

> Les sources évoluent vite. Re-vérifier avant d'implémenter chaque adaptateur.
> URLs clés : developers.tiktok.com (Content Posting, Business API),
> developers.facebook.com/docs/instagram-api, business-api.tiktok.com.

## TikTok

### Publier (Content Posting API)
- Vidéo : `POST /v2/post/publish/video/init/` (scope `video.publish`), `DIRECT_POST`.
- Photo : `POST /v2/post/publish/content/init/` (scope `video.publish`), `DIRECT_POST`, `PHOTO`.
- Source média : `FILE_UPLOAD` (chunked upload, `upload_url` valable 1 h) **ou**
  `PULL_FROM_URL` (le domaine/prefix d'URL doit être **vérifié** par l'app).
- Limites : **6 requêtes/min par token utilisateur** ; caps quotidiens de posts
  (`spam_risk_too_many_posts`, `reached_active_user_cap`, max 5 shares en attente/24 h).
- **⚠️ Audit :** un client non audité publie en **« private viewing mode »**
  uniquement. Pour lever la restriction, l'app doit passer l'audit TikTok.
- **`is_aigc=true`** : à mettre sur tout contenu généré par IA (tag « IA » dans la
  description — obligation de transparence). Champs `brand_*` pour le paid partnership.
- Description : max 4000 runes UTF-16 (photo) ; titre max 90 runes.

### Commentaires (Business API — TikTok for Business, business-api.tiktok.com)
- **Répondre** à un commentaire sur une vidéo possédée : OUI (endpoints
  « Reply to a comment », « Reply to an existing comment on an owned video »).
- **Lire/récupérer** les commentaires : OUI via Business API (export de commentaires,
  webhooks « mentions »).
- **Liker un commentaire** : endpoints existants (TikTok Business API + Instagram
  Graph API), mais l'automatisation de likes est interdite par les CGU
  (« inauthentic behavior », bots) → **fonction retirée de l'outil**.
- Webhooks : « Subscribe to mentions via Webhooks API » (mentions, pas tous les
  commentaires de manière prouvée).

### Insights / Performance (Business API — endpoints organiques)
- Insights du compte possédé : OUI (métriques vidéo, followers, engagement,
  « Get TTO Public Account Insights »).
- **NON disponible** via API officielle : démographie d'audience (âge/genre/lieu),
  watch time détaillé, sources de trafic, liste followers/following, données
  concurrents, données hashtag/trending (le Research API est réservé aux
  chercheurs académiques).
- → La vue « Performance » TikTok est donc **post-centric** (views, likes,
  comments, shares) sans démographie.

### ToS / automation
- Bots non officiels interdits (follow/unfollow, auto-like, DM massif, scraping).
- L'automatisation « allowed » est gated par l'API officielle (Business Account).
- Règle d'or : répondre uniquement aux gens qui ont engagé en premier.

## Instagram

### API recommandée : Instagram Login (graph.instagram.com)
- OAuth 2.0 direct avec les credentials Instagram (sans page Facebook). Comptes
  **Business/Creator uniquement** (pas les comptes personnels).
- **Si une page Facebook est requise** (ou si on passe par la route Graph API
  `graph.facebook.com`), on ajoute un **Facebook Login** dans Settings :
  l'utilisateur connecte sa page, puis on passe par le dev portal Meta. Instagram
  Login reste le chemin par défaut (plus simple, pas de page requise).
- Tokens : short-lived (1 h) → long-lived (60 j) → refresh.
- Standard Access (ses propres comptes) : pas de review. Advanced Access
  (tiers) : Meta App Review.

### Permissions utiles
| Permission | Usage |
|---|---|
| `instagram_business_basic` | lire profil, médias, commentaires, mentions |
| `instagram_business_content_publish` | publier image, carrousel, reel, story |
| `instagram_business_manage_comments` | créer/répondre/**liker**/masquer/supprimer commentaires |
| `instagram_business_manage_messages` | DMs (initié par l'utilisateur) |
| `instagram_business_manage_insights` | analytics compte + médias |

### Publier
- Via **URLs publiquement accessibles** (pas d'upload direct) → on héberge le média
  sur notre VPS et on fournit l'URL.
- Reel = 3 appels (créer container → poll `FINISHED` → publish).
- Carrousel jusqu'à 10 items ; stories (Business only).

### Commentaires
- **Lire, répondre, liker, masquer, supprimer** les commentaires sur ses posts : OUI.
- Webhooks pour les mentions/commentaires (réal-time).

### Insights
- Insights médias + compte : OUI. Démographie d'audience : OUI (**si 100+
  followers**). Top 45 villes/pays, âge, genre.

## Conséquences d'architecture

1. **Community manager** : complet sur Instagram (lire/répondre/liker). Sur TikTok :
   répondre/lire oui (Business API), **like non disponible** → feature like
   désactivée/à confirmer côté TikTok.
2. **Performance** : riche sur Instagram (démographie incluse) ; post-centric sans
   démographie sur TikTok.
3. **Publier TikTok** : nécessite un **audit de l'app**. Pour le MVP démo, adapter
   `mock` ; pour la prod client, une app TikTok unique + audit (les clients
   autorisent leurs comptes via OAuth).
4. **PULL_FROM_URL** : prévoir un domaine vérifié (le VPS sert les médias avec un
   prefix d'URL vérifié côté app).
5. **Anti-spam** : répartir les batchs (commentaires/likes) dans le temps ;
   respecter les rate limits (6 req/min TikTok par token ; ~200 req/h Instagram).

## Sources

- developers.tiktok.com/doc/content-posting-api-get-started (+ reference direct-post / photo-post)
- business-api.tiktok.com/portal (comment management, insights)
- developers.facebook.com/docs/instagram-api
- getphyllo.com/post/instagram-api-guide (Instagram Login vs Graph API)
- rapiddevelopers.com (limitations comment moderation TikTok)
- smartreply.io/blog/can-you-automate-replies-instagram (ToS replies)
