from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url
from sqlmodel import Session, SQLModel, create_engine, select

import personify.config as config

engine: Engine | None = None


def get_engine() -> Engine:
    global engine
    if engine is None:
        engine = create_engine(config.settings.db_url, echo=False, pool_pre_ping=True)
    return engine


def reset_engine() -> None:
    global engine
    if engine is not None:
        engine.dispose()
    engine = None


def _ensure_postgres_database() -> None:
    url = make_url(config.settings.db_url)
    if not url.get_backend_name().startswith("postgresql"):
        return
    db_name = url.database
    if not db_name:
        return
    admin_url = url.set(database="postgres")
    admin_engine = create_engine(
        admin_url.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": db_name},
            ).scalar()
            if not exists:
                quoted = conn.dialect.identifier_preparer.quote(db_name)
                conn.execute(text(f"CREATE DATABASE {quoted}"))
    finally:
        admin_engine.dispose()


def init_db() -> None:
    """Create the pgvector extension and all tables."""
    _ensure_postgres_database()
    db_engine = get_engine()
    if db_engine.dialect.name == "postgresql":
        with db_engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    # Import models so SQLModel.metadata is populated.
    from personify import models  # noqa: F401

    SQLModel.metadata.create_all(db_engine)
    seed_sources()


def seed_sources() -> None:
    """Ensure the sources table reflects the parser registry."""
    from personify.models import Source
    from personify.parsers import PARSERS

    with Session(get_engine(), expire_on_commit=False) as s:
        existing = {x.slug for x in s.exec(select(Source)).all()}
        for slug in sorted(PARSERS):
            if slug not in existing:
                s.add(Source(slug=slug, label=slug.title()))
        s.commit()


@contextmanager
def session_scope() -> Iterator[Session]:
    s = Session(get_engine(), expire_on_commit=False)
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    with Session(get_engine(), expire_on_commit=False) as s:
        yield s
