"""The tree-sitter engine, shared by every first-class provider.

tree-sitter is the good path, and it is chosen over Python's stdlib ``ast`` -- which is
free and exact -- for two reasons that both matter more than exactness:

* it parses **syntactically broken files**, the normal state of a tree an agent is halfway
  through editing;
* one interface then serves every language with a grammar, so adding a language is a
  package rather than a second parser.

A subclass supplies the grammar module and the node types; nothing here knows any syntax.
The grammar is loaded lazily and a missing one raises at construction, which
``registry.py`` turns into a warning and a fallback (rule 3).
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Iterator
from typing import Any

from relore.code.api import DEFS, EXTENTS, REFS, Definition, Reference


class TreeSitterProvider:
    """Base class. Subclasses set the four class attributes below."""

    name: str = "tree-sitter"
    patterns: tuple[str, ...] = ()
    capabilities: frozenset[str] = frozenset({DEFS, EXTENTS, REFS})

    #: The pip distribution that carries the grammar, e.g. ``tree_sitter_python``.
    grammar_module: str = ""
    #: Node types that introduce a definition, mapped to the ``kind`` reported for them.
    definition_nodes: dict[str, str] = {}
    #: Node types that count as a reference, mapped to ``(kind, name_field)``: the kind
    #: reported for it, and the field carrying the name when the node is not itself the
    #: name. ``None`` means the node's own text. The field is resolved *recursively*, so a
    #: call whose callee is an attribute chain reports the chain's last component -- the
    #: name a reader would have written -- without core knowing what a chain is.
    reference_nodes: dict[str, tuple[str, str | None]] = {}
    #: Strongest reading first. Two node types routinely describe the same occurrence: a
    #: call whose callee is an attribute is a ``call`` node and an ``attribute`` node over
    #: the same bytes. The caller wants one row per occurrence, labelled with the most
    #: specific of them.
    reference_precedence: tuple[str, ...] = ()

    def __init__(self) -> None:
        self._parser = _parser_for(self.grammar_module)

    # -- the interface ---------------------------------------------------

    def defs(self, path: str, source: bytes) -> Iterable[Definition]:
        """Definitions in one file, in source order.

        A file with a syntax error still yields the definitions before *and* after the
        break: tree-sitter produces a tree with ERROR nodes rather than refusing, and
        walking past them is the whole reason this is not the stdlib parser.
        """
        root = self._parser.parse(source).root_node
        return list(self._walk(root, source, parents=()))

    def refs(self, path: str, source: bytes) -> Iterable[Reference]:
        if REFS not in self.capabilities:
            return []
        root = self._parser.parse(source).root_node
        return list(self._references(root, source))

    # -- walking ---------------------------------------------------------

    def _walk(self, node: Any, source: bytes, *, parents: tuple[str, ...]) -> Iterator[Definition]:
        for child in node.children:
            kind = self.definition_nodes.get(child.type)
            if kind is None:
                yield from self._walk(child, source, parents=parents)
                continue
            name = self._name_of(child, source)
            if not name:
                yield from self._walk(child, source, parents=parents)
                continue
            qualname = self.qualname(parents, name)
            yield Definition(
                qualname=qualname,
                name=name,
                kind=self.kind_for(kind, parents),
                start_line=child.start_point[0] + 1,
                # Only when declared: an end line a caller cannot trust is worse than
                # none, because `enclosing_symbol` would present a guess as exact.
                end_line=child.end_point[0] + 1 if EXTENTS in self.capabilities else None,
                parent=self.qualname(parents[:-1], parents[-1]) if parents else None,
            )
            yield from self._walk(child, source, parents=(*parents, name))

    def _references(self, node: Any, source: bytes) -> Iterator[Reference]:
        """Every occurrence of a name, once, in source order, labelled with its kind.

        Keyed on the *name's* byte span rather than on the node, because that is what
        makes one occurrence one row however many node types describe it -- and it is what
        lets a definition, an attribute read and a call all be reported without the caller
        having to guess which of three rows is the same line twice.
        """
        rank = {kind: index for index, kind in enumerate(self.reference_precedence)}
        found: dict[tuple[int, int], Reference] = {}
        for child in _descendants(node):
            entry = self.reference_nodes.get(child.type)
            if entry is None:
                continue
            kind, field = entry
            named = self._name_node(child, field)
            if named is None:
                continue
            name = _text(named, source)
            if not name:
                continue
            key = (named.start_byte, named.end_byte)
            previous = found.get(key)
            if previous is None or rank.get(kind, len(rank)) < rank.get(previous.kind, len(rank)):
                found[key] = Reference(name=name, line=named.start_point[0] + 1, kind=kind)
        for key in sorted(found):
            yield found[key]

    def _name_node(self, node: Any, field: str | None) -> Any | None:
        """Follow ``name_field`` down to the node that carries the written name."""
        if field is None:
            return node
        child = node.child_by_field_name(field)
        if child is None:
            return None
        entry = self.reference_nodes.get(child.type)
        return self._name_node(child, entry[1]) if entry else child

    def _name_of(self, node: Any, source: bytes) -> str:
        field = node.child_by_field_name("name")
        return _text(field, source) if field is not None else ""

    # -- the parts a language owns ---------------------------------------

    def qualname(self, parents: tuple[str, ...], name: str) -> str:
        """Join a name to its enclosing scopes. **Rule 1: the provider owns this.**

        The default is a dot, which is right for a great many languages and wrong for
        several; a provider whose language spells it ``::`` or ``#`` overrides this and core
        is none the wiser.
        """
        return ".".join((*parents, name))

    def kind_for(self, kind: str, parents: tuple[str, ...]) -> str:
        return kind


def _descendants(node: Any) -> Iterator[Any]:
    """The node and everything under it. Order is irrelevant: references are sorted by
    their name's position on the way out."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)


def _parser_for(module_name: str) -> Any:
    """Load a grammar and build a parser, or raise so the registry can degrade.

    Both imports are deferred: ``tree-sitter`` is the ``[code]`` extra and each grammar is
    its own per-language extra, so neither may be imported at module scope (section 2).
    """
    if not module_name:
        raise RuntimeError("a TreeSitterProvider subclass must set grammar_module")
    import tree_sitter

    grammar = importlib.import_module(module_name)
    return tree_sitter.Parser(tree_sitter.Language(grammar.language()))


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")
