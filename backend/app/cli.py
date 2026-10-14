"""Local account administration."""

import argparse
import getpass
import os

from .store import Store


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["add-user"])
    parser.add_argument("username")
    args = parser.parse_args()
    password = getpass.getpass("Password (at least 6 characters): ")
    store = Store(os.environ.get("DATABASE_PATH", "/data/jobs.sqlite"))
    store.create_user(args.username, password)
    print(f"Created {args.username}")


if __name__ == "__main__":
    main()
