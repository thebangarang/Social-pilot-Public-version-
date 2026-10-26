# Social Pilot — version publique

> **EN —** A whitelabel, self-hosted platform for social-media production: an LLM-driven "team" proposes posts,
> community replies and performance views; **the human validates every step**. FastAPI + SQLAlchemy, 95 routes,
> 44 test files, **322 tests** (320 green on a clone without `.env`, 2 skipped). **Built by directing an AI coding agent — architecture, design and review
> by me.** Published as a portfolio demonstration, not a commercial product.

Plateforme **whitelabel**, **self-hosted** (une instance par client, sur son propre serveur), qui centralise la
**production de posts**, le **pilotage de la communauté** et le **suivi de performance** sur TikTok, Instagram,
LinkedIn, Facebook, Threads, Bluesky et Pinterest.

**[La visite en images, écran par écran →](docs/screens.md)** — cinq captures, une phrase chacune.

L'utilisateur s'adresse à une « équipe » virtuelle (SEO, graphiste, monteur, marketing, growth, community manager)
pilotée par un modèle, puis garde le contrôle final : **l'agent propose, l'humain valide — à chaque étape, sans
exception.**

## Ce que contient le dépôt

| espace | ce qu'il fait |
|---|---|
| **Génération** | chat + import de médias + branding kit + presets de direction artistique → propositions numérotées → génération média → scan de conformité → aperçu par réseau → double approbation → publication immédiate ou programmée |
| **Performance** | tableau de bord global et par publication, modèle de métriques unifié, agrégé ou par plateforme |
| **Community manager** | boîte des commentaires, classification vert / orange / rouge, réponses générées puis validées, lot réparti dans le temps |
| **Réglages** | connexion des réseaux (OAuth), fournisseur de modèle (OpenRouter, compatible OpenAI, Ollama local ou distant), budget et consommation, utilisateurs, 2FA, thème |

**Chiffres mesurés** : 54 fichiers applicatifs, **95 routes HTTP**, 44 fichiers de test, **322 tests** — 320 au
vert et 2 marqués `skip` sur un clone sans `.env`, parce qu'ils exigent des identifiants TikTok (la route répond
503 par conception, ce n'est pas une panne). Suite complète en 3 min 06. 7 plateformes branchées.

## Comment ce dépôt a été construit — à lire avant de juger le code

**Je ne suis pas développeur au sens classique.** J'ai conçu ce produit, arbitré son architecture, revu chaque
écran et chaque décision, et je l'ai fait construire par un agent de codage (Hermes) que je pilote, vérifie et
corrige. Ce dépôt est donc une démonstration de **conception, de direction technique et de vérification** — pas de
frappe au clavier.

Ce que je peux défendre, et que je défendrai en entretien :

- **l'architecture** : FastAPI + SQLAlchemy, un module d'authentification + 2FA, un `net_guard` qui refuse les
  appels sortants vers les adresses internes (SSRF), un `breaker` par fournisseur, un `usage_meter` par instance ;
- **les décisions** : pourquoi l'approbation humaine est obligatoire à chaque étape, pourquoi le fournisseur de
  modèle est un réglage et non une constante, pourquoi une instance par client ;
- **les erreurs** : elles sont écrites dans `decisions.md`, y compris celles qui ont coûté du temps.

## Statut, sans enjolivement

**Construit et testé, jamais vendu.** Il a été écrit à la demande d'un partenaire, pour ses propres clients, puis
mis de côté après une vérification de marché : des SaaS éprouvés faisaient déjà ce travail pour ces agences, à
quelques dizaines d'euros par mois, et leur intégration de l'IA était une question de temps, pas de verrou
technique. Le code est publié comme **démonstration de ce que je sais concevoir seul**, pas comme un produit à
vendre ni une promesse de support.

## Lancer

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
make demo                     # base + mode démo + compte de démonstration
make dev                      # http://127.0.0.1:8100
```

`make demo` affiche l'e-mail et le mot de passe du compte de démonstration.

## Mode démo — le parcours complet, sans une seule clé

Sans clé d'API et sans aucun compte réseau, **le produit se visite de bout en bout** :

| ce qui est simulé | comment, et pourquoi c'est visible |
|---|---|
| génération de texte | un fournisseur `demo` rend des propositions déterministes, marquées `[démo]` |
| génération d'image | une vignette est dessinée localement (Pillow), portant la mention « MODE DÉMO » |
| tri des commentaires | vert / orange / rouge, déterministe, sans modèle |
| publication | l'adaptateur factice rend un identifiant et une URL `mock.*` — rien ne part sur un réseau |
| performance | des métriques fabriquées localement, déterministes, croissantes avec le temps |

Le mode démo se lit dans les Réglages comme fournisseur `demo` : il ne peut pas être confondu avec une vraie
génération, ni présenté comme telle. Pour repasser en réel : choisir un fournisseur dans les Réglages et
renseigner une clé.

Vérification du mode démo, hors interface :

```bash
.venv/bin/python scripts/verify_demo_mode.py
```

Ce script appelle les mêmes fonctions que l'application — parseur de propositions, génération d'image, tri des
commentaires, adaptateur de publication — et échoue si l'une des cinq étapes casse ou si un appel réseau part.

Aucune donnée client, aucun secret, aucune adresse d'infrastructure ne figure dans ce dépôt. Les médias, les
sauvegardes et les bases ont été retirés de cette copie et sont exclus par `.gitignore`.

## Structure

```
app/       FastAPI — api/, services/, agents/, llm/, platforms/, auth, net_guard, breaker, usage_meter
tests/     44 fichiers de test (322 cas)
docs/      spec, architecture, roadmap, APIs par plateforme, modèle analytique
site/      site public (accueil, confidentialité, CGU)
SECURITY.md · decisions.md · project-context.md
```

## Docs clés

- `docs/spec.md` — spécification fonctionnelle complète (les 4 espaces, le pipeline).
- `docs/architecture.md` — architecture, modèle de données, surface d'API.
- `docs/platform-apis.md` — contraintes réelles des APIs TikTok / Instagram / LinkedIn.
- `docs/analytics-model.md` — modèle de métriques unifié.
- `decisions.md` — décisions d'architecture (ADR), y compris celles qui ont mal tourné.
- `SECURITY.md` — posture sécurité : classes de données, refus, routage.

## Ce que je referais autrement

1. **Une seule source de vérité pour les capacités par plateforme.** Les différences TikTok / Instagram / LinkedIn
   sont réparties entre trois fichiers ; un registre déclaratif unique aurait évité deux régressions.
2. **Les tests avant l'écran.** Deux boucles d'interface ont été refaites parce que le contrat de données n'était
   pas figé. Le test d'abord aurait coûté une heure au lieu d'une journée.
3. **Un dépôt public dès le premier jour.** Un historique propre vaut mieux qu'un nettoyage après coup — c'est
   exactement le travail qu'il a fallu faire pour publier cette version.

## Langue

UI et textes clients : **français d'abord** (EN en option). Code, docs techniques et commits : anglais.

## Licence

Publié pour **démonstration et revue**. Aucune licence d'usage, de modification ou de redistribution n'est
accordée. Les icônes et marques des réseaux appartiennent à leurs propriétaires.

---

*Kevin Lauharatanavisais — [github.com/thebangarang](https://github.com/thebangarang) · Sophia-Antipolis*
