# Conformité plateformes (TikTok / Instagram) — CGU & ToS

Position de principe : **l'humain décide, l'API officielle exécute.** Aucune
action automatisée sur les plateformes. Chaque publication, réponse ou
modération passe par une API officielle et exige une validation humaine. Pas de
scraping, pas de bot non officiel.

## Retiré

- **Like auto** (liker automatiquement x % des commentaires) — retiré. Les
  endpoints de like existent bien (Instagram Graph API, TikTok Business API),
  mais l'automatisation de likes est explicitement interdite par les CGU des deux
  plateformes (« inauthentic behavior », « automated bots »). Risque de suspension
  du compte du client. → Supprimé du code, de l'UI et des docs.

## Conforme (et pourquoi)

| Fonction | Statut | Justification |
|---|---|---|
| Lire les commentaires | OK | Endpoint officiel (Instagram « get comments », TikTok « Get comments »). |
| Répondre à un commentaire (validation humaine) | OK | Endpoint officiel (Instagram « reply to comments », TikTok « Reply to a comment »). L'humain relit et clique « envoyer » — le modèle Hootsuite/Sprout/Buffer. |
| Modération (masquer/supprimer) | OK | Endpoint officiel des deux plateformes. |
| Publier un post (immédiat ou programmé) | OK | Endpoint officiel (TikTok Content Posting API, Instagram Graph API). Le scheduling est standard et autorisé. |
| Lire les insights | OK | Endpoint officiel (Instagram Graph API, TikTok Accounts Insights). |
| Classification LLM (vert/orange/rouge) | OK | Analyse interne, aucune action plateforme — simple aide à la décision. |
| Brouillon de réponse généré par LLM | OK | Le brouillon est relu et approuvé par un humain avant envoi. Aucun envoi automatique. |

## Limite (à surveiller avant les vrais adaptateurs)

- **Batch de réponses** — envoyer N réponses d'un coup. Chaque réponse est relue
  et approuvée par un humain (conforme sur le fond), mais un envoi groupé
  instantané ressemble à un bot. À faire quand les adaptateurs réels seront
  branchés :
  1. **Throttle** (répartition temporelle) — espace les envois (ex. 1 réponse /
     30-60 s), déjà noté dans la roadmap.
  2. Respecter les **rate limits** des API (chaque plateforme limite les appels
     par token).
- **Déclaration IA** — TikTok exige `is_aigc=true` sur le contenu généré par IA
  (déjà noté dans `docs/platform-apis.md`). À câbler dans l'adaptateur TikTok.

## Jamais à implémenter

- Follow/unfollow automatique.
- DM massif (messages directs non sollicités).
- Scraping du web (on n'utilise que l'API officielle).
- Like ou commentaire automatisé sans validation humaine.

## Références

- Instagram Platform (Meta) : « get / reply / delete / hide comments » — developers.facebook.com.
- TikTok Business API : « Reply to a comment », « Like/unlike a comment » — business-api.tiktok.com.
- TikTok ToS : interdiction des « automated bots » et « inauthentic commercial behaviors ».
