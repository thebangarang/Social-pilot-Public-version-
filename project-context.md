# project-context.md — Social Pilot

## Vision

Le « dream tool des community managers nouvelle génération branché sur l'IA ».
Une équipe virtuelle (SEO, graphiste, monteur, marketing, growth, CM) dans une
seule interface, semi-automatique et pilotée par un humain qui ne garde que
l'esprit critique : **l'agent propose, l'humain valide**, à chaque étape.

## Cible

Gérants de marques, business web, community managers, agences. Vendu en
whitelabel : chaque client reçoit sa propre instance, sur son propre VPS, à ses
couleurs.

## Positionnement

- **Whitelabel + self-hosted** : données et tokens chez le client, pas chez un SaaS
  tiers. Argument vie privée + souveraineté.
- **Local-first possible** : LLM local (Ollama) en option = edge « privacy ».
- **Une seule clé pour tout** : OpenRouter pour les LLM **et** la génération
  média (image nano-banana/Flux, vidéo Veo/Wan). Budget + auto/manual top-up.

## Périmètre plateformes

- **MVP : TikTok + Instagram.** Architecture d'adaptateurs extensible (Twitter/X,
  LinkedIn, YouTube, Facebook, Threads, Pinterest plus tard).
- **Contraintes réelles** (détaillées dans `docs/platform-apis.md`) :
  - TikTok : post via Content Posting API (audit de l'app requis pour visibilité
    publique), commentaires/réponses via Business API, insights via Business API,
    **pas de like de commentaire confirmé, pas de démographie audience**.
  - Instagram : Instagram Login (graph.instagram.com) — publish, commentaires
    (répondre/liker/masquer), insights, DMs. Comptes Business/Creator uniquement.

## Les 4 espaces

1. **Génération** — pipeline complet de production (voir `docs/spec.md`).
2. **Performance** — métriques unifiées, agrégées et par plateforme.
3. **Community manager** — inbox commentaires + classification vert/orange/rouge.
4. **Settings** — connexions, LLM, budget, admin users, 2FA, thème.

## Principes non négociables

1. **L'agent propose, l'humain valide.** Jamais de publication sans double
   approbation explicite. L'agent intégré est sandboxé aux fonctions de
   l'interface (pas de shell, pas de code).
2. **Whitelabel dès le départ.** Aucune couleur en dur ; thème piloté par variables.
3. **Respect des ToS.** Pas de comportement spammy (like/comment massif). Répartition
   des batchs dans le temps. Le but : ne jamais se faire bloquer.
4. **Apprentissage continu.** L'agent mémorise le style, le ton et les retours de
   l'utilisateur pour s'améliorer (profil de marque + log de feedback + exemples
   approuvés réinjectés dans les prompts).
5. **Sécurité Tier 1** (auth + OAuth + surface publique). Voir `SECURITY.md`.

## MVP visé

Un démo fonctionnel du pipeline de bout en bout : Génération (chat → propositions
→ validation → média → compliance → preview → double approbation → programmation),
Settings (connexions + LLM + thème), une base de Performance et de Community
manager. La publication réelle peut passer par l'adaptateur `mock` tant que les
apps TikTok/Instagram ne sont pas auditées — l'expérience utilisateur est
identique.

## Hors périmètre (pour l'instant)

- Autopilot autonome (viendra après entraînement/retour utilisateur).
- Multi-tenant dans une même instance (une instance = un client).
- Paiement intégré (le top-up OpenRouter/fal se fait côté fournisseur).
