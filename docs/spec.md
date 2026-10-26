# docs/spec.md — Spécification fonctionnelle

Périmètre : MVP TikTok + Instagram, whitelabel, une instance par client.
Principe transversal : **l'agent propose, l'humain valide** — à chaque étape,
sans exception. L'agent intégré est sandboxé aux fonctions de l'interface.

---

## Tab 1 — Génération

Pipeline de production de posts en 8 étapes.

### 1.1 Le chat

- **Sessions persistantes** : chaque conversation est une session sauvegardée
  (titre, fil complet, propositions et publications liées). L'utilisateur crée
  une nouvelle session, ou **revient à tout moment** sur une session passée
  (rehydratation du fil). Les travaux passés restent accessibles.
- Champ de saisie conversationnel : l'utilisateur formule sa demande comme à une
  équipe (SEO, graphiste, monteur, marketing, growth, CM).
- **Import content** : bouton d'upload **photo / vidéo / gif / audio / doc** —
  accepter un maximum d'extensions (jpg, png, webp, heic, gif, mp4, mov, webm,
  mkv, avi, m4a, mp3, wav, pdf, ...). À l'ingestion : validation (magic bytes +
  extension + taille) puis **conversion/transcodage vers un format standard**
  (images → jpeg/png/webp, vidéo → mp4 H.264, audio → mp3/m4a) via ffmpeg/Pillow.
- Les médias importés rejoignent la **médiathèque** et sont joignables à une
  demande de post (réinterprétation, montage, insertion branding).

### 1.2 Branding asset kit

- Dossier par défaut (« Branding kit ») : logo(s), couleurs, typographies,
  visuels, guidelines. L'utilisateur charge une première fois des éléments → le
  tool les considère comme son **branding kit par défaut**.
- Access rapide depuis l'interface : consulter, modifier (ajouter/supprimer),
  et **joindre au prompt de génération** (l'agent lit le kit pour respecter
  l'identité visuelle).
- Le kit alimente aussi le thème whitelabel (couleurs du client).

### 1.3 Presets de direction artistique (optionnel)

- Presets de style sélectionnables avant de lancer la génération (ex. « minimal
  clean », « bold & playful », « premium dark », ...). Quelques presets livrés
  par défaut ; chaque preset = un bloc de directives de style injecté dans le
  prompt de l'agent.
- **Le portfolio se construit à l'usage** : l'agent propose régulièrement de
  **sauvegarder la DA d'une publication validée comme preset réutilisable**.
  Les presets affinés par l'utilisateur sont la partie la plus précieuse — c'est
  son template portfolio, qui grandit au fil des demandes.

### 1.4 Génération de propositions (PAS de génération média ici)

- L'agent (connecté au LLM choisi) génère :
  - les **textes** (caption, hashtags, titre, accroche, CTA, **emojis** quand
    c'est pertinent ou que ça habille le post),
  - des **propositions de prompts de génération média** (description précise du
    visuel/vidéo à produire), **sans générer le média** (coût LLM/image élevé —
    on veut d'abord l'approbation du concept).
- **Chaque proposition porte un numéro.** Plusieurs propositions (variantes).
- **Emojis** : utilisés avec discernement ; l'agent apprend le niveau attendu au
  fil des propositions approuvées/ajustées (mémoire de marque).

### 1.5 Ajustements

- L'utilisateur sélectionne un numéro et demande des ajustements (« refais la 2
  avec un ton plus punchy, hashtags plus courts »).
- L'agent refait des propositions → boucle jusqu'à validation.

### 1.6 Validation du concept + génération média

- Une fois une version validée, l'agent **génère les médias** (image ou vidéo)
  OU **utilise les médias importés** selon les instructions validées :
  intégration du branding, montage des vidéos importées, réinterprétation des
  images dans le style demandé. Flexible mais **fidèle au concept validé**.
- La génération média passe par le provider configuré (fal.ai/Replicate).

### 1.7 Scan compliance (par l'agent)

- L'agent scanne les médias + textes : **copyright / propriété intellectuelle /
  règles des réseaux sociaux** (contenu interdit, claims trompeurs, marques
  tierces visibles, watermark, musique non licenciée, contenu choquant).
- Sortie : verdict + liste de risques. Si risque → retour ajustement. Tag
  `is_aigc`/transparence IA géré automatiquement.

### 1.8 Sélection des réseaux + previews simulées

- L'utilisateur choisit les réseaux (TikTok et/ou Instagram).
- **Previews simulées** : rendu fidèle du post dans le contexte de chaque réseau
  (mock d'UI TikTok / Instagram) pour voir à quoi ressemblera la publication.

### 1.9 Validation finale → recap → double approbation → programmation

- Validation / ajustements (→ retour génération+compliance si ajustement).
- **Recap** : « la publication ressemble à ça, elle sera postée sur tel(s)
  réseau(x) — envoyer ? ».
- **Double approbation** (anti-missclick) avant tout envoi réel.
- **Option calendrier** : date précise heure/minute. La publication est mise en
  attente puis postée automatiquement au déclenchement.
- **Programmation** : dès qu'un post est programmé, l'item apparaît dans le tab
  Performance avec le statut **« en attente de publication »** (pas encore de lien
  à partager). Le lien n'est fourni qu'après publication effective.
- Après publication : **liens d'accès rapide** vers le post + vers sa page de
  monitoring (tab Performance).

---

## Tab 2 — Performance

Suivi des performances, modèle unifié (détails : `docs/analytics-model.md`).

- **Dashboard global** : KPIs agrégés toutes plateformes (followers, croissance,
  engagement rate, reach, impressions, vues, top posts) + tendances 30 j vs 30 j.
- **Sous-items** : détail par post (séries temporelles, vs médiane, leaderboard).
- **Vues** : agrégé (toutes plateformes) OU plateforme par plateforme (TikTok /
  Instagram séparés).
- **Filtres** : plateforme, compte, période.
- Le but : aider l'utilisateur à **affiner ses productions** (best time, format
  gagnant, longueur caption, hashtags, anomalies).

---

## Tab 3 — Community manager

Inbox des commentaires (Instagram complet ; TikTok lecture + réponse via Business
API, **like non disponible** — cf. `docs/platform-apis.md`).

- **Like auto** (option, curseur en %) : like x % des commentaires, priorité aux
  commentaires positifs. Désactivé/absent côté TikTok.
- **Réponse par commentaire** : proposition générée par l'agent, validée en un
  clic (ou pré-approuvée selon le niveau).
- **Batch** : sélection multiple → valider plusieurs réponses d'un coup ; le
  système **répartit l'envoi dans le temps** (anti spam-trigger, « smooth »).
  (Sous réserve des ToS : batch via API officielle uniquement, throttle appliqué.)
- **Classification en 3 niveaux** (par l'agent + règles de mots-clés) :
  - 🟢 **Vert** — réponse simple, pré-approuvée automatiquement.
  - 🟠 **Orange** — réponse générée, validation obligatoire.
  - 🔴 **Rouge** — plainte, remboursement, menace, sujet juridique/crise →
    **transfert humain**, proposition de réponse cordiale mais validation
    obligatoire.
- L'utilisateur traite les priorités sans trier mentalement.

---

## Tab 4 — Settings

- **Connexion réseaux sociaux** : boutons « Connect » (OAuth TikTok / Instagram
  Login ; **Facebook Login en option** si une page Facebook est requise — route
  Graph API via le dev portal Meta) ; gestion/révocation des comptes connectés ;
  affichage statut token.
- **Fournisseur LLM** : local (Ollama — l'utilisateur renseigne l'**IP + port**
  du device qui fait tourner Ollama, local ou distant), cloud privé (base_url +
  clé), **OpenRouter** (une clé = tous les modèles, texte + image + vidéo).
  Sélection des modèles par défaut (texte, image, vidéo).
- **Budget / consommation** : pour OpenRouter, limite budgétaire mensuelle
  optionnelle + top-up **manual ou auto** ; suivi visuel de la consommation, du
  budget restant et de l'historique (graphiques). (Le top-up réel se fait côté
  OpenRouter ; on affiche et on bloque localement en cas de dépassement.)
- **Gestion admin des users** (admin only) : créer/éditer/désactiver/supprimer,
  reset 2FA, rôles admin/operator.
- **Sécurité (tous les users, y compris non-admin)** : changement de son propre
  mot de passe, activation/désactivation de son 2FA (Google Authenticator).
- **Thème** : choix parmi plusieurs couleurs (whitelabel) — variables CSS,
  prévisualisation live.
- **Sauvegarde** : sauvegarde automatique planifiée (DB + branding kit +
  médiathèque + réglages), avec rotation et restauration. Le tool est un espace
  de production — rien ne se perd.

---

## Apprentissage de l'agent (transversal)

- **Profil de marque** : style, ton, audience, guidelines — mis à jour au fil de
  l'usage.
- **Log de feedback** : quelles propositions ont été validées/ajustées/rejetées,
  et pourquoi.
- **Exemples approuvés** réinjectés dans les prompts (few-shot) → l'agent
  comprend de mieux en mieux le style et les attentes de l'utilisateur.
- Le tout **par compte/instance** (jamais partagé entre clients).

## Contraintes de l'agent (non négociable)

- Limité aux fonctions de l'interface : génération, media-prompts, scan
  compliance, classification, rédaction de réponses. **Aucun accès au code, au
  shell, ni au système de fichiers hors médiathèque.** Pas de modification de
  l'interface.

## Autopilot (hors MVP)

- À terme : mode plus autonome, **après** entraînement de l'agent et validation du
  niveau de satisfaction. Toujours dans le respect strict des ToS (pas de
  comportement bloquable).
