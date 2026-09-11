"""Console-script shims, so a missing extra is a sentence rather than a traceback.

``pip install ghlore`` is the *client* (AGENTS.md invariant 1: no driver, no server, no
parser), and it registers both scripts. So ``ghlored`` exists on a client-only install and
its first statement imports SQLAlchemy — which used to end in a raw ``ModuleNotFoundError``
traceback with the reader's own stack in it (huggingface/ghlore#19).

The tree-sitter path already does the right thing here and this copies it: name the cause,
name the remedy, and name **what still works**. That last clause is the one that matters to
an agent — a traceback hides the fact that every client verb is still available against a
remote daemon, so a session that could have continued stops.

This module is imported by nothing else and imports nothing at module scope: it is the
outermost frame of a console script, and the import it guards has to happen inside the
function for the guard to exist at all.
"""

from __future__ import annotations

#: Every module the daemon needs that the client install does not carry. Named here so the
#: hint can say which extra to install rather than which module was missing -- `pydantic`
#: is not a package anybody installs by name to run `ghlored`.
_SERVER_MODULES = ("sqlalchemy", "fastapi", "uvicorn", "pydantic", "psycopg")


def ghlored() -> int:
    """``ghlored``, with the client-only install told what it is missing."""
    try:
        from ghlore.daemon import main
    except ModuleNotFoundError as exc:
        missing = (exc.name or "").split(".")[0]
        if missing not in _SERVER_MODULES:
            raise
        raise SystemExit(
            f"ghlored: the daemon's dependencies are not installed (no module named "
            f"{missing!r}). Install them with:\n"
            f"    pip install 'ghlore[server]'      # a laptop index on SQLite\n"
            f"    pip install 'ghlore[postgres]'    # the deployment's database\n"
            f"The client verbs -- ghlore search / thread / inflight / status -- need none "
            f"of this and work against a daemon somebody else is running."
        ) from None
    return main()
