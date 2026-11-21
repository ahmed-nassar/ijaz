"""Database engine and session helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def make_engine(url: str) -> Engine:
    return create_engine(url, future=True)


def init_db(engine: Engine) -> None:
    """Create tables, and indexes added since a table was first created.

    Idempotent, so it is safe in the deploy's release command. Migrations (Alembic)
    come once the schema settles; until then only new tables and indexes are added here.
    """
    Base.metadata.create_all(engine)
    for table in Base.metadata.sorted_tables:
        existing = _index_names(engine, table.name)
        for index in table.indexes:
            if index.name not in existing:
                index.create(engine)


def _index_names(engine: Engine, table: str) -> set[str]:
    """Index names on a table, including expression indexes (which reflection skips)."""
    if engine.dialect.name == "postgresql":
        query = text("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema() AND tablename = :t")
    elif engine.dialect.name == "sqlite":
        query = text("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = :t")
    else:
        raise NotImplementedError(engine.dialect.name)
    with engine.connect() as conn:
        return set(conn.scalars(query, {"t": table}))


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
