#!/usr/bin/env python3
"""Create a Social Pilot user (admin or operator) from the CLI.

Usage:
  .venv/bin/python scripts/create_user.py --email admin@example.com --name "Admin" --role admin
  .venv/bin/python scripts/create_user.py --email me@example.com --name "Me"   # prompts for password
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Operator
from app.security import hash_password


def main():
    ap = argparse.ArgumentParser(description="Create a Social Pilot user.")
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--role", choices=["admin", "operator"], default="operator")
    ap.add_argument("--password", default=None, help="omit to prompt (recommended)")
    args = ap.parse_args()

    password = args.password or getpass.getpass("Password (min 8 chars): ")
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        sys.exit(1)

    email = args.email.strip().lower()
    db = SessionLocal()
    try:
        if db.scalar(select(Operator).where(func.lower(Operator.email) == email)):
            print(f"User {email} already exists.")
            sys.exit(1)
        op = Operator(
            email=email,
            name=args.name.strip(),
            role=args.role,
            password_hash=hash_password(password),
        )
        db.add(op)
        db.commit()
        db.refresh(op)
        print(f"Created {op.role} '{op.name}' <{op.email}> (id {op.id})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
