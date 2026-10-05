"""Local operator-only CLI. Passwords enter through a prompt, never command arguments."""

import argparse
import getpass
import os

from sqlalchemy import select

from table_review.accounts.service import AccountService, InvalidAccount
from table_review.storage.db import create_session_factory
from table_review.storage.models import AccountRow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("create", "reset", "revoke"))
    parser.add_argument("username")
    args = parser.parse_args()
    factory = create_session_factory(os.environ["TABLE_REVIEW_DATABASE_URL"])
    service = AccountService(factory)
    if args.action == "create":
        service.create(args.username, getpass.getpass("New password: "))
    else:
        with factory() as session:
            account = session.scalar(
                select(AccountRow).where(AccountRow.username == args.username.strip().lower())
            )
            if account is None:
                raise InvalidAccount("Account unavailable")
            owner_id = account.id
        if args.action == "reset":
            service.reset_password(owner_id, getpass.getpass("New password: "))
        else:
            service.revoke(owner_id)
    print("Account operation completed")
    factory.kw["bind"].dispose()


if __name__ == "__main__":
    main()
