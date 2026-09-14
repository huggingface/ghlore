"""The language-provider interface, and the four rules that make it survive a second
language (section 9).

Core knows no syntax. A provider claims a set of filenames and answers two questions about
one file's bytes.

**1. The provider owns the qualified name, end to end.** Core must never join parts with a
separator, because the separator *is* the language: ``Class.method``, ``pkg.Type.Method``,
``ns::Class::method``, ``Mod#fun/2``. Above this interface ``qualname`` is an opaque
string -- stored in ``documents.enclosing_symbol``, matched against ``thread_symbols``,
printed to the caller. Core compares it and never parses it.

**2. Capabilities are declared, and degradation is per tier.** Enclosing-symbol needs
``extents``; a provider without them falls back to the nearest preceding definition, which
is usually right and is **marked inferred** rather than presented as exact. ``refs`` is
optional throughout: ``relore refs`` on a language that lacks it says so and exits 0.

**3. Grammars are never a base dependency.** A provider whose grammar is not installed is
skipped with a warning, not an import error, and the ctags fallback covers its files.

**4. A provider is a pure function of ``(path, bytes)``.** No filesystem walk, no git, no
configuration, no network. That is what lets the same provider run against a working tree
in the client and against a historical blob in the daemon, and what makes it testable from
a fixture string.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

#: The three tiers a provider may declare. Nothing is all-or-nothing: a provider with
#: ``defs`` alone is a lower-fidelity answer, never a missing one.
DEFS = "defs"
EXTENTS = "extents"
REFS = "refs"
CAPABILITIES = frozenset({DEFS, EXTENTS, REFS})


@dataclass(frozen=True)
class Definition:
    """One definition. ``qualname`` is built by the provider and is opaque to core."""

    qualname: str
    name: str
    kind: str  # free text; "class"/"function"/"method" by convention
    start_line: int
    end_line: int | None = None  # None unless the provider declares `extents`
    parent: str | None = None


@dataclass(frozen=True)
class Reference:
    """One occurrence of a name.

    ``kind`` is the honest half, and it is why ``refs`` can be trusted for a sweep. A
    reference index that reports only call sites answers "find every affected site" with a
    small fraction of them and no way to tell -- measured at 21 of ~400 for
    ``compute_default_rope_parameters`` in ``huggingface/transformers``, missing the
    ``self.compute_default_rope_parameters`` in the file being debugged, because a value
    read through an attribute is not a call. So every occurrence is reported and *labelled*
    instead: ``call``, ``definition``, ``attribute``, ``name``. The kinds a language has
    are the provider's business (rule 1); core groups by them and prints the counts.
    """

    name: str
    line: int
    qualname: str | None = None  # None when the provider cannot resolve the callee
    kind: str = "reference"


@dataclass(frozen=True)
class Enclosing:
    """What line ``N`` of a file is *inside* -- the lens's whole job (section 1).

    ``exact`` is the honest half. With ``extents`` the answer is the definition whose range
    contains the line. Without them it is the nearest preceding definition, which is
    usually right and occasionally names the function above the one you meant; a caller
    that cannot tell those apart will quote the wrong symbol with full confidence.
    """

    qualname: str
    kind: str
    start_line: int
    exact: bool


@runtime_checkable
class LanguageProvider(Protocol):
    name: str
    patterns: tuple[str, ...]  # ("*.py", "*.pyi")
    capabilities: frozenset[str]

    def defs(self, path: str, source: bytes) -> Iterable[Definition]: ...

    def refs(self, path: str, source: bytes) -> Iterable[Reference]: ...


class MissingParser(Exception):
    """No provider can read this file, and the reason is an install rather than a bug.

    Carried as an exception so the CLI can print an install hint instead of an
    ``ImportError`` traceback (section 2, AGENTS.md).
    """
