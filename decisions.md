# decisions.md — Social Pilot (Architecture Decision Records)

## ADR-001 — Langage & framework : Python 3.14 + FastAPI

**Décision.** FastAPI + SQLAlchemy 2.0, même socle que command-center.
**Raison.** Réutilise directement le module auth+2FA (skill `auth-2fa-module`),
le pattern de services et de tests ; une seule équipe/stack à maintenir.
**Alternatives.** Node/NestJS (écosystème Postiz), Django. Écartées : pas de
réutilisation de l'auth, deuxième stack à sécuriser.

## ADR-002 — Base de données : SQLite (WAL) pour le MVP

**Décision.** SQLite fichier local `data/social-pilot.db`, mode WAL.
**Raison.** Une instance = un client = un seul tenant. SQLite supprime la gestion
d'un serveur Postgres séparé (simplicité d'opérations, backup = copie de fichier,
restauration triviale), suffit amplement pour un worker unique (scheduler +
polling). La couche DB reste portable vers Postgres si un client grossit.
**Alternative.** PostgreSQL (utilisé par command-center pour le multi-tenant).
Non retenu ici : pas de multi-tenant dans la même instance.

## ADR-003 — Frontend : HTML server-rendered + vanilla JS (ES modules) + SSE

**Décision.** SPA mono-page sans étape de build Node. FastAPI sert le HTML ;
le JS vanilla (modules ES) gère l'UI ; **SSE** pour le streaming du chat ; Chart.js
pour les graphiques.
**Raison.** Un seul artefact déployable (`uvicorn` sert tout), cohérent avec le
pattern command-center et le principe « simple-first ». Le SSE couvre le streaming
des propositions, le drag-drop se fait en JS natif.
**Alternative.** React/Vite + FastAPI. Écartée pour le MVP (coût de build, deux
artefacts) ; réévaluée si l'UI devient trop riche.

## ADR-004 — Agent intégré : function-caller sandboxé, PAS un agent shell

**Décision.** L'« agent » du tool est un orchestrateur serveur : un ensemble de
system prompts spécialisés (équipe SEO/graphiste/monteur/marketing/CM) + un
toolset interne (génération, media-prompt, scan compliance, classification,
rédaction de réponses) exposé uniquement via l'API de l'app. **Aucun accès shell,
fichier (hors médiathèque) ou code.**
**Raison.** Exigence explicite : l'agent est limité aux fonctions de l'interface.
C'est un agent de domaine à function-calling, pas un agent de code.
**Apprentissage.** Profil de marque + log de feedback + exemples approuvés
réinjectés dans les prompts (few-shot + mémoire de marque). Pas de fine-tuning MVP.

## ADR-005 — Fournisseur LLM : abstraction « provider », OpenRouter par défaut

**Décision.** Une couche `llm` avec 3 providers : `ollama` (local, adresse IP+port
vers le device), `openai_compatible` (cloud privé, base_url + clé), `openrouter`
(une clé = tous les modèles, top-up manual/auto côté OpenRouter). Choix par
l'utilisateur dans Settings, stocké par instance.
**Raison.** Couvre exactement les trois cas demandés. Le tool route **chaque
génération vers le modèle/source le plus adapté** (un même post peut mobiliser
plusieurs sources : texte via un modèle, image via un autre, vidéo via un
troisième). Chaque appel respecte la matrice de confidentialité LLM/GDPR
(the internal routing standard) : local/EU-privé par défaut pour les assets
sensibles, cloud uniquement sur choix explicite. Budget/limites côté OpenRouter +
garde-fou local (soft limit mensuelle).

## ADR-006 — Génération média : même couche provider, PAS de service tiers

**Décision.** La génération média (image ET vidéo) passe par la **même couche
provider** que le texte. OpenRouter couvre les trois modalités : image
(nano-banana, Flux, Seedream), vidéo (Veo, Wan). **Une seule clé/compte pour
tout.** ComfyUI local en option (privacy). Pour le MVP vidéo : priorité au
**montage** des médias importés (ffmpeg) ; la génération vidéo from-scratch est un
recours, pas le défaut.
**Raison.** Retour d'expérience AITurn : nano-banana via OpenRouter a surpassé
ComfyUI pour les images avec texte + logo intégré. Simplicité : l'utilisateur ne
gère qu'un compte payant. fal.ai / Replicate écartés (aucune valeur MVP).
**Coût.** La génération reste coûteuse → déclenchée **qu'après validation du
concept** (pipeline), et comptée dans le budget/usage OpenRouter.

## ADR-007 — Plateformes : adaptateurs + mock

**Décision.** Interface `PlatformAdapter` (publish, comments, insights) avec un
adaptateur `mock` (démo de bout en bout sans credentials ni app review) et des
adaptateurs réels TikTok + Instagram, additifs.
**Raison.** Le post TikTok réel exige un audit de l'app (visibilité publique) ;
Instagram exige une Meta/Instagram app review. Le mock rend le MVP présentable
immédiatement sans blocage externe, et l'UX reste identique.

## ADR-008 — Publication : double approbation + scheduler minute

**Décision.** État `approved` → recap → **double confirmation explicite** →
`ready` (programmé) ou `publishing` (immédiat). Un scheduler asyncio publie les
posts `ready` dont l'heure est atteinte (précision minute). Idempotence par post
(clé `post_id` + état) pour ne jamais double-poster.
**Raison.** Anti-missclick ; publication différée fiable.

## ADR-009 — Thème whitelabel : variables CSS pilotées par une table

**Décision.** Couleurs/identité dans une table `theme` + variables CSS
(`--sp-accent`, `--sp-bg`, ...). Plusieurs presets ; override client. Style
neutre/minimaliste, légère touche futuriste (quelques FX subtils), PAS le HUD
Enders Game.
**Raison.** Whitelabel : personnalisation aux couleurs du client sans toucher au code.

## ADR-010 — Langue : français d'abord

**Décision.** UI/textes clients FR (EN toggle en option). Code, docs, commits EN.
**Raison.** Convention établie (AITurn FR), cible FR/EN bilingue.

## ADR-011 — Persistance & sauvegarde automatique

**Décision.** Le tool est un espace de production : rien ne doit se perdre.
Sauvegarde automatique planifiée (DB + branding kit + médiathèque + réglages)
vers un répertoire local versionné, optionnellement répliqué sur un bucket
S3-compatible (B2/S3), avec rotation et restauration en un geste.
**Raison.** « Le tool devient son bureau » — la persistance est non négociable.
Pattern réutilisé : the internal backup standard (git + backups versionnés
+ checkpoints).

## ADR-012 — Sessions de chat persistantes

**Décision.** Chaque conversation est une `chat_session` persistante (titre,
dates, contexte). L'utilisateur crée une session, y revient à tout moment
(rehydratation du fil + propositions + publications liées), ou en ouvre une
nouvelle. Les travaux passés restent attachés à leur session.
**Raison.** L'utilisateur doit pouvoir revenir sur ses travaux passés — c'est un
espace de travail, pas un prompt jetable.

## ADR-013 — Consommation : composant usage-meter vendu, jamais un proxy

**Décision.** Le registre de consommation devient un composant vendu
(`app/usage_meter.py` + `app/model_prices.json`, source
`~/projects/_templates/usage-meter/`) : coût du fournisseur fait foi, sinon carte de
prix épinglés et datée, sinon ligne marquée `unknown` et loguée. Aucun proxy LLM
embarqué (LiteLLM ou autre) dans une instance client.
**Raison.** Le coût qui fait foi arrive déjà dans la réponse ; un proxy serait un
second service à durcir et à mettre à jour sur chaque VPS client, pour une
meilleure précision nulle — et il apporte ses propres trous (LiteLLM a enregistré
`spend: 0.0` sur des requêtes de 53 725 tokens, issue #24204).
**Alternatives écartées.** Proxy LiteLLM + couche comptable (recommandation du
scan de marché) : à reprendre seulement si un système a besoin de plafonds ou
d'une application stricte que nous ne contrôlons pas ; le registre resterait la
source de facturation dans ce cas.
**Preuve que le trou était réel.** Les 47 lignes du 2026-09-05 de la base de dev
ont toutes un coût nul avec des tokens renseignés : notre propre code enregistrait
un zéro silencieux jusqu'au correctif du 06/09 à 08:18. C'est ce que l'auto-test
par forme d'appel doit empêcher de reproduire.
**Conséquence.** Un relevé ne sort pas pour une période contenant des lignes
`unknown` ou sans provenance ; l'ancienne historique est étiquetée `provider`
(coût > 0, lu chez le fournisseur) ou `unknown` (coût nul, non mesuré).

## ADR-014 — Identité de build, publiée sur l'API de gestion

**Décision.** Chaque image embarque le commit qui l'a produite (`GIT_SHA`,
`BUILD_DATE`, `BUILD_DIRTY` en arguments de build → `SP_BUILD_*`), lu par
`app/build_info.py` et publié sur `/api/v1/management/health` sous `build{}`.
`/health` public reste nu.
**Raison.** Le 2026-09-24, la production servait `/management/health` et `/stats`
mais renvoyait 404 sur `/usage` : l'image de production datait du 2026-09-20, soit
quatre commits de retard, et rien sur l'instance ne pouvait le dire —
`/opt/social-pilot` n'est même pas un dépôt git. La dérive a été découverte par un
endpoint manquant, sur une instance client.
**Alternatives écartées.** (a) La constante applicative (`APP_VERSION = "0.1.0"`) :
elle ne change pas d'un build à l'autre, donc elle n'identifie rien. (b) Exposer le
commit sur `/health` public : divulgation inutile à un appelant anonyme ; la
détection de dérive est authentifiée, l'identité l'est aussi. (c) Lire git à
l'exécution depuis le conteneur : le code n'est pas copié avec le dépôt dans
l'image.
**Conséquence.** La dérive se lit en un appel HTTP authentifié, par nous comme par
le Command Center. Un build fait depuis un arbre sale est signalé sale, et la
promotion le refuse.

## ADR-015 — Devnet : un anneau de staging sur la machine de build

**Décision.** Chaque application livrée a deux anneaux : **devnet** (base, médias,
sauvegardes et `.env` propres ; ressources plafonnées ; adaptateurs plateformes en
`mock` ; écoute sur `127.0.0.1`) et **production**. Le passage se fait par une porte
unique, the release script, pilotée par le `release.toml` du
projet.
**Raisons.** (1) On ne débogue plus sur une instance client ni en production.
(2) Deux constats de la machine : le bridge docker n'est pas joignable de façon
fiable ici — un port publié accepte la connexion TCP et ne répond jamais, sans
erreur nulle part ; la passerelle interne a rencontré le même mur et tourne en
`network_mode: host` pour cette raison. Et un conteneur en uid 10001 ne peut pas
lire un `.env` en 600 appartenant à l'opérateur sans `sudo`. Le devnet tourne donc
sur le réseau hôte, lié à `127.0.0.1`, en uid 1000, ce qui laisse aussi la base du
devnet lisible par l'opérateur.
**Alternatives écartées.** Devnet sur un VPS (coût récurrent, et le risque de
production reste sur la même machine) ; bridge docker avec port publié (ne
fonctionne pas ici) ; `chmod 644` sur le `.env` du devnet (inutile dès que le
conteneur tourne en uid 1000, et moins propre).
**Conséquence.** `release.py promote` refuse tant que `/opt/social-pilot` n'est pas
un clone git : sans version de retour il n'y a pas de rollback, donc pas de
déploiement. C'est l'état actuel, enregistré dans `release.toml`
(`git_checkout = false`) ; la conversion est décrite dans
the internal release doc et demande une fenêtre de maintenance.
