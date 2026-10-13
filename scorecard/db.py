from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base

_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def init_db(url: str | None = None) -> None:
    """Crée le moteur et les tables. Appelé au démarrage (ou par les tests avec une autre URL)."""
    global _engine, _SessionLocal
    url = url or get_settings().database_url
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    _engine = create_engine(url, **kwargs)
    Base.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)


def ensure_db() -> None:
    if _SessionLocal is None:
        init_db()


def get_session() -> Iterator[Session]:
    """Dépendance FastAPI."""
    if _SessionLocal is None:
        init_db()
    with _SessionLocal() as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Pour les scripts/CLI: commit automatique ou rollback."""
    if _SessionLocal is None:
        init_db()
    with _SessionLocal() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
