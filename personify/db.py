from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from personify.config import settings

engine = create_engine(settings.db_url, echo=False, pool_pre_ping=True)


def init_db() -> None:
    """Create the pgvector extension and all tables."""
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    # Import models so SQLModel.metadata is populated.
    from personify import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    s = Session(engine)
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    with Session(engine) as s:
        yield s
