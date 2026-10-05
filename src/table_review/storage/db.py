import re

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

type SessionFactory = sessionmaker[Session]


def create_session_factory(database_url: str, *, schema: str | None = None) -> SessionFactory:
    connect_args = {}
    if schema is not None:
        if not re.fullmatch(r"test_[0-9a-f]{32}", schema):
            raise ValueError("Invalid isolated test schema")
        connect_args["options"] = f"-csearch_path={schema}"
    engine = create_engine(
        database_url, pool_pre_ping=True, connect_args=connect_args, hide_parameters=True
    )
    if engine.dialect.name != "postgresql":
        engine.dispose()
        raise ValueError("PostgreSQL is required for revision/lease concurrency")
    return sessionmaker(engine, expire_on_commit=False)
