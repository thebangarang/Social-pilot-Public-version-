#!/usr/bin/env python3
"""Generate a management token + the .env lines to add.

The token authenticates the command center to this instance's telemetry API
(GET /api/v1/management/health and /stats). Keep it secret; never paste it in
logs or chat. The management API stays disabled until SP_MANAGEMENT_TOKEN is
set in this instance's .env.
"""
import secrets


def main() -> None:
    token = secrets.token_urlsafe(32)
    print("# Add these lines to this instance's .env, then restart:")
    print(f"SP_MANAGEMENT_TOKEN={token}")
    print('SP_INSTANCE_LABEL="Client Name"')
    print()
    print("The management API is DISABLED until SP_MANAGEMENT_TOKEN is set.")


if __name__ == "__main__":
    main()
