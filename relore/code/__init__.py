"""The code lens: parsing code without indexing any of it (section 1).

Two callers, one module, and that is the whole reason this lives in this repository rather
than in a separate tool:

* **``relored``, at derive time.** Every document already knows a commit; reading the tree
  at *that* commit turns ``foo.py:412`` into ``Gemma3Model.forward``, which is what makes a
  review comment retrievable years later. The entire storage footprint is
  ``documents.commit_sha``, ``documents.enclosing_symbol`` and ``path_aliases``.
* **``relore``, on the working tree.** ``map``, ``defs`` and ``refs`` over the checkout the
  caller is sitting in.

**No source code goes into the database, and no API response returns any** (AGENTS.md
invariant 2). The reason is sharper than "agents have grep": the whole justification for a
daemon is that ``/search/issues`` is 30 requests per minute shared across the token, and
that constraint does not exist for code. A parser over the working tree costs nothing,
needs no token, and describes *the tree the caller is actually on*, dirty files included --
where a server-side code index knows ``main`` and the agent is on a feature branch
mid-edit. For code, remote is strictly worse than local.

Nothing here imports :mod:`relore.store`, :mod:`relore.github` or :mod:`relore.api`,
asserted by ``tests/unit/test_module_boundary.py``: it is a pure function of a tree, which
is exactly what lets both binaries hold it without the client inheriting a database driver.

Imports are deferred throughout -- ``tree-sitter`` is the ``[code]`` extra and each grammar
is its own per-language extra, so importing this package must not require either.
"""

from __future__ import annotations

from relore.code.api import (
    CAPABILITIES,
    DEFS,
    EXTENTS,
    REFS,
    Definition,
    Enclosing,
    LanguageProvider,
    MissingParser,
    Reference,
)

__all__ = [
    "CAPABILITIES",
    "DEFS",
    "EXTENTS",
    "REFS",
    "Definition",
    "Enclosing",
    "LanguageProvider",
    "MissingParser",
    "Reference",
]
