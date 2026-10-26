# Analyse concurrentielle — Social Pilot vs Hootsuite / Sprout Social / Buffer

Basée sur les plans et tarifs publics 2026. Social Pilot est comparé tel qu'il
est au MVP (TikTok + Instagram).

## Positionnement des trois (2026)

| Outil | Prix d'entrée | Modèle | Positionnement |
|---|---|---|---|
| **Hootsuite** | ~$99/user/mois | par user | Généraliste historique, ~10+ réseaux, gouvernance, entreprise |
| **Sprout Social** | $79/seat/mois (Essentials) → $399 (Advanced) | par seat | Premium : analytics + social listening + inbox, agences/mid-market |
| **Buffer** | Gratuit (3 canaux) ; $5-12/canal/mois | par canal | Simple/abordable, créateurs/SMB, AI Assistant |
| **Social Pilot** | Self-hosted (VPS par client) | one-time + maintenance optionnelle | Self-hosted, whitelabel, AI-native, privacy-first, TikTok+IG |

Points clés de tarification concurrente :
- Sprout est le plus cher : les add-ons (Premium Analytics, Listening, Employee
  Advocacy) sont vendus à part, non tarifés publiquement, et le Listening peut
  doubler/tripler le contrat (médiane observée ~$19k/an).
- Hootsuite n'a plus de plan gratuit ; Sprout non plus (essai 30j seulement).
- Buffer est le seul avec un vrai gratuit (3 canaux).

## Où ils sont meilleurs que nous (nos lacunes)

1. **Couverture plateforme** — 10-11 réseaux (X, LinkedIn, Facebook, YouTube,
   Pinterest, Threads, Bluesky…) contre nos 2 (TikTok + Instagram). C'est le
   fossé principal.
2. **Social listening** — Sprout/Hootsuite surveillent les mentions de marque,
   concurrents et tendances sur le web, avec analyse de sentiment. Nous : rien.
3. **Analytics profonde** — benchmarking concurrents, rapports personnalisés,
   export PDF, reporting cross-réseau, sentiment. Nous : KPIs + tendance +
   best-time + leaderboard (solide, mais sans concurrents ni exports).
4. **Collaboration d'équipe** — workflows d'approbation multi-étapes, tasking,
   gestion multi-clients pensée pour les agences. Nous : RBAC admin/operator simple.
5. **Avis Google/Trustpilot, employee advocacy, intégrations helpdesk
   (Salesforce/Zendesk), gestion des ads** — nous n'avons rien de tout ça.
6. **Écosystème d'intégrations** (Canva, Adobe, Dropbox, CRM…). Nous : rien.
7. **Maturité** — apps mobiles, SSO, SLA, support 24/5, notifications push.

## Où nous sommes meilleurs qu'eux

1. **Production de contenu réelle par un agent, pas un réécriveur.** Leur « IA »
   = réécriture de légendes (Hootsuite OwlyWriter, Sprout AI Assist, Buffer AI
   Assistant). Nous : l'agent produit un post complet structuré (caption +
   hashtags + hook + CTA + media_prompt) **et** génère l'image/vidéo réelle,
   avec le logo et les assets de marque intégrés en référence. Aucun des trois ne
   génère de média ni n'intègre le branding dans le visuel. → Différenciateur central.
2. **Pipeline de gouvernance** — scan de conformité (vérification vision du texte
   sur l'image), previews par plateforme, double approbation. Les agences qui
   gèrent des comptes clients veulent ça ; eux ont des workflows d'approbation,
   mais pas la vérification visuelle de conformité.
3. **Self-hosted + single-tenant + whitelabel** — souveraineté des données (RGPD),
   une instance par client, aucune donnée dans un SaaS US. Décisif pour les
   agences/marques UE à contraintes données ; ils ne le proposent pas du tout.
4. **UI français natif.**
5. **Modèle de coût** — self-hosted (one-time + maintenance optionnelle) vs
   per-seat/per-canal récurrent. Moins cher pour une marque gérant 2-5 comptes ;
   levier de marge pour une agence qui revend en whitelabel.
6. **Option LLM local** (edge vie privée).
7. **API de management multi-client** (télémétrie + command center) — pour notre
   scale opérationnel, sans équivalent chez eux.

## Parité

- **Publication/scheduling** : comparable (on programme à la minute ; eux ont le
  bulk et les « meilleurs horaires » prédictifs).
- **Inbox unifié** : leur « Community inbox »/« Smart Inbox » est plus mature
  (détection de collision, tagging, sentiment) ; le nôtre est plus opinioné
  (triage vert/orange/rouge + brouillons LLM) mais moins large.

## Conclusion stratégique

Ne pas se battre frontalement contre Hootsuite/Sprout : on ne rattrapera pas
10 réseaux + listening + ads + intégrations rapidement, et ce n'est pas notre
marché. Notre niche est claire et défendable :

**Social Pilot = self-hosted, whitelabel, privacy-first, AI-native, pour les
marques/agences UE qui veulent (a) la souveraineté des données, (b) un agent qui
PRODUIT du contenu de marque (pas juste des légendes), (c) de la gouvernance
(double approbation + conformité), (d) du français.**

Le vrai concurrent à surveiller n'est pas Hootsuite/Sprout, c'est **Buffer** côté
SMB + les outils « AI-native » émergents (Blotato, SocialBee…) qui misent aussi
sur l'IA de contenu. C'est là qu'il faut rester en avance sur la génération média
+ branding.

## Chantiers prioritaires pour combler l'écart

1. **Couverture plateforme** — ajouter LinkedIn / X / Facebook / YouTube en
   adaptateurs (ferme le plus gros argument de vente contre nous).
2. **Reporting client** — export PDF des rapports + vue par période (les agences
   vendent des rapports).
3. *(Plus tard)* **Social listening léger** — le plus cher, à n'attaquer qu'une
   fois le cœur solide.

Ces items existent déjà dans `docs/roadmap.md` (Post-MVP) ; ils sont ici
re-priorisés : plateforme d'abord, reporting ensuite.
