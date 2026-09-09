"""Local setup commands. Secrets are prompted and never accepted as arguments."""

from __future__ import annotations

import argparse
import getpass

from .auth import LocalIdentityStore
from .settings import PlatformSettings
from .signing import ApprovalSigner
from .storage import DurableResultStore


def bootstrap(username: str) -> None:
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("passwords do not match")
    settings = PlatformSettings()
    DurableResultStore(settings).initialize()
    identities = LocalIdentityStore(settings)
    identities.initialize()
    identities.create_approver(username, password)
    ApprovalSigner(settings).create_key()
    print("Local approver and Windows-protected signing key created.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="inspection-platform")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap_parser = commands.add_parser("bootstrap")
    bootstrap_parser.add_argument("--username", required=True)
    arguments = parser.parse_args()
    if arguments.command == "bootstrap":
        bootstrap(arguments.username)


if __name__ == "__main__":
    main()
