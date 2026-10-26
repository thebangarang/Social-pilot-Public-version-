#!/usr/bin/env python3
"""Vérification du mode démo : la vraie chaîne de parsing, sans réseau.

Ce script n'écrit rien dans le dépôt : il appelle les fonctions réelles de l'application avec un
réglage « demo » en base, et vérifie que ce que le mode démo rend est accepté par les parseurs qui
consomment habituellement un modèle.

    .venv/bin/python scripts/verify_demo_mode.py
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from app import llm  # noqa: E402
from app.agents import SYSTEM_PROMPT, _parse_proposals  # noqa: E402

echecs = []


def verifier(nom: str, condition: bool, detail: str = "") -> None:
    print(f"  {'✓' if condition else '✗'} {nom}{(' — ' + detail) if detail else ''}")
    if not condition:
        echecs.append(nom)


print("1. Connexion et modèles (aucun appel réseau attendu)")
ok, message = llm.test_connection("demo", "", None)
verifier("test_connection rend True", ok, message)
modeles = llm.list_models("demo", "", None)
verifier("list_models propose 2 entrées", len(modeles) == 2, str([m["id"] for m in modeles]))

print("\n2. Génération de propositions, relue par le parseur de l'agent")
messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": "Trois propositions pour annoncer l'ouverture de la boutique, "
                                "plateformes : tiktok, instagram, linkedin"},
]
reponse = llm.chat_completion(messages, temperature=0.8)
propositions = _parse_proposals(reponse["content"])
verifier("3 propositions parsées", len(propositions) == 3, f"{len(propositions)} reçue(s)")
premiere = propositions[0]
verifier("variants par plateforme présents", {"tiktok", "instagram", "linkedin"} <= set(premiere["variants"]))
verifier("caption non vide et marquée démo", "[démo]" in premiere["variants"]["tiktok"]["caption"])
verifier("provider tracé", reponse["provider"] == "demo", reponse["provider"])

print("\n3. Image générée localement")
png = llm.image_generation("une illustration plate de démonstration", size="512x512")
verifier("PNG valide", png[:8] == b"\x89PNG\r\n\x1a\n", f"{len(png)} octets")
try:
    from PIL import Image
    from io import BytesIO
    image = Image.open(BytesIO(png))
    verifier("dimensions demandées", image.size == (512, 512), str(image.size))
except Exception as exc:  # noqa: BLE001
    verifier("image relisible", False, str(exc))

print("\n4. Tri des commentaires")
from app.services.community import classify_batch_llm  # noqa: E402

niveaux = classify_batch_llm(["Merci, super produit !", "Quel est le prix ?",
                              "Commande jamais reçue, je veux un remboursement"])
verifier("trois niveaux rendus", len(niveaux) == 3, str(niveaux))
verifier("niveaux valides", all(n in ("green", "orange", "red") for n in niveaux), str(niveaux))

print("\n5. Publication par l'adaptateur factice")
from app.platforms import get_adapter  # noqa: E402
from types import SimpleNamespace  # noqa: E402

adaptateur = get_adapter("tiktok")
objet = SimpleNamespace(platform="tiktok", idempotency_key="demo-cle-1", remote_post_id=None,
                        caption="[démo]", media_ids=[], published_at=None)
identifiant, url = adaptateur.publish(objet)
verifier("identifiant distant rendu", bool(identifiant), identifiant)
verifier("URL factice", url.startswith("https://mock."), url)

print()
if echecs:
    print(f"ÉCHEC : {len(echecs)} vérification(s) — {', '.join(echecs)}")
    raise SystemExit(1)
print("Mode démo vérifié : génération, image, tri, publication — aucun appel réseau.")
