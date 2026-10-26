# docs/analytics-model.md — Modèle de métriques unifié

Objectif : reproduire ce que les meilleurs outils pro (Hootsuite, Metricool,
Mydrop AI, PostEverywhere) fournissent, **normalisé dans une seule architecture**
pour que l'utilisateur reste dans la même UI/UX sans friction, et que les
métriques TikTok et Instagram soient comparables.

## Principes de conception (issus du benchmark)

- **Une snapshot par (post, plateforme, timestamp)** — on poll périodiquement et on
  stocke l'historique (séries temporelles), pas un compteur écrasé.
- **Deux niveaux** : global (agrégé toutes plateformes) et par plateforme ; détail
  par post.
- **Taux d'engagement réel** : calculé par follower ET par impression (les deux
  racontent des choses différentes).
- **Données dérivées** qui aident vraiment à affiner la production : best time to
  post (calculé sur SES propres posts, pas des études génériques), format gagnant,
  longueur de caption, nb hashtags, présence de lien, longueur vidéo.
- **Anomaly detection** : flag d'outliers (≈ 2× la baseline) notés bon/mauvais.

## Métriques primaires (collectées par plateforme, quand disponibles)

| Métrique | TikTok | Instagram | Note |
|---|---|---|---|
| Impressions | ✔ (video) | ✔ | reach potentiel |
| Reach | partiel | ✔ | comptes uniques touchés |
| Likes | ✔ | ✔ | |
| Commentaires | ✔ | ✔ | |
| Partages/shares | ✔ | ✔ | |
| Saves/bookmarks | ✖ | ✔ | signal fort algorithme |
| Vues vidéo | ✔ | ✔ (reel) | |
| Watch time / completion | partiel (Business) | ✔ (reel) | rétention |
| Profil visits | partiel | ✔ | |
| Followers (total) | ✔ | ✔ | |
| Nouveaux followers (net) | ✔ | ✔ | croissance |
| Clics (link) | partiel | ✔ (bio/site) | |
| Démographie audience | ✖ | ✔ (100+ followers) | âge/genre/villes/pays |

> TikTok ne donne pas la démographie ni le watch time détaillé via l'API officielle
> → la vue TikTok est post-centric. Instagram est plus riche (démographie incluse).

## Métriques dérivées (calculées, cross-plateforme)

1. **Engagement rate (per follower)** = (likes + comments + shares + saves) / followers.
2. **Engagement rate (per impression)** = (likes + comments + shares + saves) / impressions.
3. **Follower growth** = net nouveaux followers sur période (Δ total).
4. **Views per post** (moyenne), **engagement per post** (moyenne).
5. **Best time to post** : grille jour × heure, chaque case scorée contre la
   médiane de SES propres posts (rolling 180 j), avec cases jamais testées.
6. **Format gagnant** : carrousel vs vidéo/reel vs image, classés sur ses résultats.
7. **Leaderboard** : classement de ses profils par engagement/reach/growth.
8. **Anomalies** : outliers statistiques notés bon/mauvais.

## Vues de l'onglet Performance

- **Dashboard global** : KPIs agrégés (followers totaux, croissance, engagement,
  reach, top posts) + graphiques de tendance (30 j vs 30 j précédents).
- **Filtres** : par plateforme, par compte, par période.
- **Détail par post** : séries temporelles de la métrique + comparaison à la
  médiane + placement dans le leaderboard.
- **Plateforme par plateforme** : onglets TikTok / Instagram séparés.

## Stockage

Table `metric_snapshot` : `(post_publication_id, platform, captured_at, metric_key,
value)`. Chaque poll = une ligne par métrique, timestampée. Les dérivées sont
calculées à la volée (pas stockées) pour rester cohérentes.

## Cadence de polling

- Posts < 48 h : toutes les ~2–4 h.
- Posts < 7 j : toutes les 12 h.
- Posts plus anciens : 1×/24 h (ou à la demande).
Respecter les rate limits (TikTok 6 req/min/token ; Instagram ~200 req/h).

## Références

- Hootsuite Social Analytics (liste des centaines de métriques).
- Metricool Analytics (reach, engagement, CTR unifiés).
- Mydrop AI Performances (modèle le plus complet : watch time, saves, direction
  clicks, best-time grid sur ses propres posts, leaderboard, anomalies).
- PostEverywhere (filtres plateforme/compte/période, engagement rate auto).
