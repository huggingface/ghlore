"""A provider for a language that does not exist, registered from the test suite.

This fixture is what keeps section 9's extension point real. It is deliberately **not** a
tree-sitter provider and its separator is deliberately **not** a dot:

* not tree-sitter, so the interface is proven to be the interface rather than a description
  of one implementation;
* ``::`` for scope, so any place core joined or split a qualname itself would produce
  ``Shape.area`` and fail loudly instead of accidentally agreeing (rule 1).

The toy language: a line starting with ``shape NAME`` opens a scope, ``do NAME`` defines
something inside it, and ``use NAME`` is a reference. Indentation is irrelevant; a scope
runs until the next ``shape``.
"""

from __future__ import annotations

from collections.abc import Iterable

from relore.code.api import DEFS, EXTENTS, REFS, Definition, Reference


class ToyProvider:
    name = "toy"
    patterns = ("*.toy",)
    capabilities = frozenset({DEFS, EXTENTS, REFS})

    def defs(self, path: str, source: bytes) -> Iterable[Definition]:
        lines = source.decode("utf-8", "replace").splitlines()
        opened: list[tuple[int, str]] = []  # (index, name) of each `shape`
        found: list[Definition] = []
        for index, line in enumerate(lines, start=1):
            words = line.split()
            if len(words) >= 2 and words[0] == "shape":
                opened.append((index, words[1]))
                found.append(
                    Definition(
                        qualname=words[1],
                        name=words[1],
                        kind="shape",
                        start_line=index,
                        end_line=None,  # filled below, once the next shape is known
                    )
                )
            elif len(words) >= 2 and words[0] == "do" and opened:
                scope = opened[-1][1]
                found.append(
                    Definition(
                        qualname=self.qualname(scope, words[1]),
                        name=words[1],
                        kind="deed",
                        start_line=index,
                        end_line=index,
                        parent=scope,
                    )
                )
        return _close_shapes(found, len(lines))

    def refs(self, path: str, source: bytes) -> Iterable[Reference]:
        out = []
        for index, line in enumerate(source.decode("utf-8", "replace").splitlines(), start=1):
            words = line.split()
            if len(words) >= 2 and words[0] == "use":
                out.append(Reference(name=words[1], line=index))
        return out

    def qualname(self, scope: str, name: str) -> str:
        """``::``, because the separator is the language (rule 1)."""
        return f"{scope}::{name}"


class ToyDefsOnlyProvider(ToyProvider):
    """The same language at a lower tier, for the degradation this fixture exists to test.

    Declaring no ``extents`` means ``enclosing_symbol`` must fall back to the nearest
    preceding definition and **mark the answer inferred** -- and it must not quietly read
    the ``end_line`` values this class still happens to produce.
    """

    name = "toy-defs-only"
    capabilities = frozenset({DEFS})

    def defs(self, path: str, source: bytes) -> Iterable[Definition]:
        """Report no end lines, because none are declared.

        The conformance suite insists on this, and it caught this fixture inheriting
        extents it had disclaimed. An end line a caller cannot trust is worse than none:
        ``enclosing_symbol`` would present the nearest-preceding guess as exact.
        """
        return [
            Definition(
                qualname=d.qualname,
                name=d.name,
                kind=d.kind,
                start_line=d.start_line,
                end_line=None,
                parent=d.parent,
            )
            for d in super().defs(path, source)
        ]

    def refs(self, path: str, source: bytes) -> Iterable[Reference]:
        """Nothing, because no reference tier is declared.

        Core already guards on the declaration before calling this, so the guard here is
        belt and braces -- but the conformance suite requires it, and a provider that
        answers what it disclaimed is a provider whose declaration nobody can trust.
        """
        return []


def _close_shapes(found: list[Definition], last_line: int) -> list[Definition]:
    """Give each ``shape`` an extent running to just before the next one."""
    starts = [d.start_line for d in found if d.kind == "shape"]
    out: list[Definition] = []
    for definition in found:
        if definition.kind != "shape":
            out.append(definition)
            continue
        later = [s for s in starts if s > definition.start_line]
        end = (min(later) - 1) if later else last_line
        out.append(
            Definition(
                qualname=definition.qualname,
                name=definition.name,
                kind=definition.kind,
                start_line=definition.start_line,
                end_line=end,
                parent=definition.parent,
            )
        )
    return out
