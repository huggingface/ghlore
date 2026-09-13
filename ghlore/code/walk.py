"""Finding the files to parse in a working tree.

This is the one part of :mod:`ghlore.code` that touches a filesystem, and it is separate
from the providers on purpose: section 9's rule 4 makes a provider a pure function of
``(path, bytes)``, which is what lets the same provider run here against a dirty working
tree and inside ``ghlored`` against a historical blob. Give a provider a walker and that
stops being true.

Nothing is cached. A cached repo map is a staleness hazard aimed at exactly the property
that justifies doing this locally -- that the answer describes *the tree the caller is
actually on*, dirty files included -- and parsing a Python repository is fast enough not to
need one (section 1).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

#: Directories never worth walking. Not a gitignore parser: this runs in whatever
#: directory the caller is sitting in, and the cost of being wrong is a slower map rather
#: than a wrong one.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "build",
        "dist",
        "site-packages",
        ".tox",
        ".eggs",
    }
)

#: A file bigger than this is a vendored bundle, a fixture or a generated blob, and parsing
#: it costs more than the one symbol it might contribute.
MAX_FILE_BYTES = 2_000_000


def source_files(root: str) -> Iterator[str]:
    """Every file under ``root`` some provider claims, in a stable order.

    A file path is also accepted, so ``ghlore refs`` and ``ghlore map`` work on one file.
    """
    from ghlore.code.registry import claimed

    if os.path.isfile(root):
        if claimed(root):
            yield root
        return
    for directory, subdirs, names in os.walk(root):
        subdirs[:] = sorted(d for d in subdirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(names):
            path = os.path.join(directory, name)
            if claimed(path):
                yield path


def all_files(root: str) -> Iterator[str]:
    """Every file under ``root`` worth reading, claimed by a provider or not.

    :func:`source_files` is the *parsing* set, so it depends on which grammars are
    installed. A text search must not: "which files have this shape" is asked of YAML and
    docs too, and an answer that silently omits them is worse than a slower one.
    """
    if os.path.isfile(root):
        yield root
        return
    for directory, subdirs, names in os.walk(root):
        subdirs[:] = sorted(d for d in subdirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(names):
            if not name.startswith("."):
                yield os.path.join(directory, name)


def read(path: str) -> bytes | None:
    """A file's bytes, or ``None`` when it is too big or unreadable.

    Unreadable is not an error here: a repo map that dies on one broken symlink is a repo
    map nobody can run, and the missing file is one symbol rather than a wrong answer.
    """
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None
