#!/usr/bin/env python3
"""Bascule l'instance en mode démo : fournisseur « demo » + un compte administrateur local.

Aucun appel réseau, aucune clé, aucune donnée réelle. À lancer après `make setup` :

    python scripts/demo_setup.py
    make demo

Ce que ce script fait, et rien de plus :

1. crée la base si elle n'existe pas (`scripts/init_db.py`) ;
2. met la ligne `llm_settings` en provider « demo » (une seule ligne, singleton) ;
3. crée un administrateur de démonstration si aucun utilisateur n'existe ;
4. affiche l'URL, l'e-mail et le mot de passe à saisir.

Le mode démo est *visible* : il s'affiche dans les Réglages comme fournisseur « demo », et les
contenus générés portent la mention [démo]. Il ne peut donc pas se confondre avec une vraie
génération, ni être présenté à un client comme telle.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

DEMO_EMAIL = "demo@socialpilot.local"
DEMO_PASSWORD = "demo-social-pilot"
DEMO_NAME = "Démo"


def main() -> int:
    base = RACINE / "data" / "social-pilot.db"
    if not base.exists():
        print("[démo] base absente — initialisation")
        subprocess.run([sys.executable, "scripts/init_db.py"], cwd=RACINE, check=True)

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import LLMSetting, Operator

    db = SessionLocal()
    try:
        reglage = db.scalar(select(LLMSetting).limit(1))
        if reglage is None:
            reglage = LLMSetting()
            db.add(reglage)
        reglage.provider = "demo"
        reglage.base_url = ""
        reglage.api_key_enc = None
        reglage.model = "demo-text"
        reglage.image_model = "demo-image"
        reglage.video_model = "demo-image"
        reglage.is_active = True
        db.commit()
        print("[démo] fournisseur de modèle : demo (aucun réseau)")

        utilisateurs = db.scalars(select(Operator)).all()
        if utilisateurs:
            print(f"[démo] {len(utilisateurs)} utilisateur(s) déjà présent(s) — aucun compte créé")
        else:
            db.close()
            sous = subprocess.run(
                [sys.executable, "scripts/create_user.py",
                 "--email", DEMO_EMAIL, "--name", DEMO_NAME, "--role", "admin",
                 "--password", DEMO_PASSWORD],
                cwd=RACINE, check=False,
            )
            db = SessionLocal()
            if sous.returncode != 0:
                print("[démo] la création du compte a échoué — créez-le avec `make admin`")
    finally:
        db.close()

    print("\n[démo] prêt :")
    print(f"        make dev        → http://127.0.0.1:8100")
    print(f"        e-mail          {DEMO_EMAIL}")
    print(f"        mot de passe    {DEMO_PASSWORD}")
    print("[démo] la génération, les images et la publication sont simulées ;")
    print("       les contenus produits portent la mention [démo].")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
