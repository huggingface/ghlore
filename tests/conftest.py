"""The dialect-parity fixture.

Every store-level test runs twice: once against in-memory SQLite, once against Postgres.
Postgres is **skipped, never silently passed**, when no server is configured -- a green
suite that only ever ran on SQLite would be a green suite about the wrong database
(the build plan section 12).

    RELORE_TEST_POSTGRES_URL=postgresql://localhost/relore_test make test
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from relore.store.dialect import make_engine  # noqa: E402
from relore.store.migrations import migrate  # noqa: E402
from relore.store.schema import metadata  # noqa: E402

POSTGRES_ENV = "RELORE_TEST_POSTGRES_URL"


@pytest.fixture(params=["sqlite", "postgresql"])
def engine(request: pytest.FixtureRequest) -> Iterator[Engine]:
    if request.param == "sqlite":
        engine = make_engine("sqlite://")
    else:
        url = os.environ.get(POSTGRES_ENV)
        if not url:
            pytest.skip(f"set {POSTGRES_ENV} to run the Postgres half of the parity suite")
        engine = make_engine(url)
        _drop_everything(engine)
    migrate(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _drop_everything(engine: Engine) -> None:
    """Only our own tables, by name -- never `DROP SCHEMA`, in case someone points this
    at a database that has other things in it."""
    with engine.begin() as conn:
        for table in reversed(metadata.sorted_tables):
            conn.execute(text(f'DROP TABLE IF EXISTS "{table.name}" CASCADE'))
