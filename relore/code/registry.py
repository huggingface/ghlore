"""Which provider reads which file, and what happens when none can.

Providers are discovered through the ``relore.languages`` entry point, so **adding a
first-class language is a package, not a patch to core** (section 9). The tests prove that
seam with a provider for a toy language registered from the test suite with no core diff --
without that test the extension point is a paragraph in a document.

Two degradation rules, and they are the reason a language is never *unsupported*, only
lower fidelity:

* a provider whose grammar is not installed is **skipped with a warning**, not an import
  error;
* any file no provider claims falls to **ctags**, which knows ~40 languages and no
  extents, so the lens drops one tier (enclosing symbol becomes the nearest preceding
  definition, marked inferred) instead of returning nothing.

Discovery is cached, because it reads entry points and probes for grammars, and both
answers are fixed for the life of the process.
"""

from __future__ import annotations

import fnmatch
import logging
import os
from functools import lru_cache

from relore.code.api import LanguageProvider, MissingParser

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "relore.languages"

#: Set to any non-empty value to leave the ctags fallback out. Exists for the conformance
#: suite, which has to be able to observe a first-class provider's own answers rather than
#: a fallback's.
DISABLE_CTAGS_ENV = "RELORE_NO_CTAGS"


@lru_cache(maxsize=1)
def providers() -> tuple[LanguageProvider, ...]:
    """Every usable first-class provider, in entry-point order."""
    out: list[LanguageProvider] = []
    for entry in _entry_points():
        try:
            factory = entry.load()
            provider = factory()
        except Exception as exc:  # noqa: BLE001 -- rule 3: a warning, never an ImportError
            log.warning(
                "language provider %r is registered but unusable (%s: %s); "
                "its files fall back to ctags at defs-only fidelity",
                entry.name,
                exc.__class__.__name__,
                exc,
            )
            continue
        out.append(provider)
    return tuple(out)


@lru_cache(maxsize=1)
def ctags_provider() -> LanguageProvider | None:
    """The breadth fallback, or ``None`` when no universal-ctags is on PATH.

    BSD ctags -- the one macOS ships as ``/usr/bin/ctags`` -- has no ``--output-format``,
    so its presence proves nothing. The provider probes for the real thing and reports
    itself unavailable otherwise.
    """
    if os.environ.get(DISABLE_CTAGS_ENV):
        return None
    from relore.code.providers.ctags import CtagsProvider

    provider = CtagsProvider()
    if not provider.available():
        log.info("no universal-ctags on PATH; unclaimed files have no provider")
        return None
    return provider


#: The message for "nothing here can read code". One string, because it is the answer to
#: several different questions and they must not drift apart.
INSTALL_HINT = (
    "no language provider is installed. pip install 'relore[python]' -- or put "
    "universal-ctags on PATH for definitions in ~40 languages at lower fidelity."
)


def require_any() -> None:
    """Raise unless *something* can read code.

    Called by the verbs that walk a tree, and the reason is a failure that looked like an
    answer: with no provider installed, nothing claims any file, so the walk visits nothing
    and the map comes back empty and exit 0. An empty result is the right answer to a quiet
    corpus and the wrong one to a missing install.
    """
    if not providers() and ctags_provider() is None:
        raise MissingParser(INSTALL_HINT)


def provider_for(path: str) -> LanguageProvider:
    """The provider for ``path``, or raise :class:`MissingParser` with an install hint."""
    for provider in providers():
        if claims(provider, path):
            return provider
    fallback = ctags_provider()
    if fallback is not None:
        return fallback
    raise MissingParser(f"no language provider for {path!r}: {INSTALL_HINT}")


def claims(provider: LanguageProvider, path: str) -> bool:
    name = os.path.basename(path)
    return any(fnmatch.fnmatch(name, pattern) for pattern in provider.patterns)


def claimed(path: str) -> bool:
    """Whether *some* provider would read this file. Used by the repo map to decide what
    to walk, without paying for a parse to find out."""
    if any(claims(provider, path) for provider in providers()):
        return True
    fallback = ctags_provider()
    return fallback is not None and claims(fallback, path)


def reset() -> None:
    """Drop the discovery cache. For tests that register a provider at runtime."""
    providers.cache_clear()
    ctags_provider.cache_clear()


def _entry_points() -> tuple:
    from importlib.metadata import entry_points

    try:
        return tuple(entry_points(group=ENTRY_POINT_GROUP))
    except TypeError:  # pragma: no cover -- the pre-3.10 selectable-API spelling
        return tuple(entry_points().get(ENTRY_POINT_GROUP, ()))
