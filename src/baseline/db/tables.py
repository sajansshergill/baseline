"""SQLAlchemy table objects.

The migrations are the single source of truth for the schema. Instead of re-typing every
column here (and risking the two drifting apart), we read the table definitions from the
database the first time each one is needed.
"""

from __future__ import annotations

import warnings

from sqlalchemy import Engine, MetaData, Table

from baseline.db.session import get_engine

_metadata = MetaData()


def table(name: str, engine: Engine | None = None) -> Table:
    if name in _metadata.tables:
        return _metadata.tables[name]
    with warnings.catch_warnings():
        # SQLAlchemy doesn't know pgvector's type; we only ever write vectors with raw SQL.
        warnings.filterwarnings("ignore", message="Did not recognize type 'vector'")
        return Table(name, _metadata, autoload_with=engine or get_engine())


def reset_cache() -> None:
    """Forget reflected tables (used by tests after re-running migrations)."""
    _metadata.clear()