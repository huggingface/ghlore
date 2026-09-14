"""``store/dialect.py`` is the only module that may know which database it is talking to.

This is the test AGENTS.md points at. Without it "the dialect seam" is a comment, and the
first `if postgres` sprinkled into the repository layer is how a portable core stops being
portable -- quietly, and only on the dialect nobody ran.
"""

from __future__ import annotations

import pathlib

import pytest

PKG = pathlib.Path(__file__).resolve().parents[2] / "relore"

# dialect.py *is* the seam. migrations legitimately branches: the Postgres-only search
# layer has to be skipped somewhere, and a migration step is the honest place.
MAY_BRANCH = {"store/dialect.py", "store/migrations/__init__.py"}

MARKERS = ("dialect.name", "is_sqlite(", "from sqlalchemy.dialects import")


def _sources() -> list[tuple[str, str]]:
    return [(str(path.relative_to(PKG)), path.read_text()) for path in sorted(PKG.rglob("*.py"))]


@pytest.mark.parametrize("marker", MARKERS)
def test_only_the_seam_knows_the_dialect(marker: str) -> None:
    offenders = [name for name, body in _sources() if marker in body and name not in MAY_BRANCH]
    assert offenders == [], (
        f"{marker!r} appears outside the dialect seam: {offenders}. "
        "Add the branch to relore/store/dialect.py instead."
    )


def test_the_seam_actually_exists() -> None:
    """Guard against the markers passing because the files were renamed away."""
    names = {name for name, _ in _sources()}
    assert names >= MAY_BRANCH


def test_portable_index_spellings_are_not_a_branch() -> None:
    """``sqlite_where=`` / ``postgresql_where=`` in the schema are deliberate.

    A partial index has no portable spelling in SQLAlchemy, so both are passed and each
    dialect ignores the other's. That is one declaration, not a code path, which is why
    the markers above do not catch it and why it is allowed to live in schema.py.
    """
    body = (PKG / "store" / "schema.py").read_text()
    assert "sqlite_where=" in body and "postgresql_where=" in body
    assert "dialect.name" not in body
