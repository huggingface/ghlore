"""The breadth fallback: universal-ctags, definitions only, ~40 languages.

This provider is the reason **a language is never unsupported, only lower fidelity**
(section 1). A repository in a language nobody has written a provider for still gets
definitions, and the lens degrades one tier -- enclosing symbol becomes the nearest
preceding definition, marked inferred -- instead of returning nothing.

Two facts about it are easy to get wrong:

* **``ctags`` on PATH proves nothing.** macOS ships BSD ctags as ``/usr/bin/ctags``, which
  has no ``--output-format`` and a different output format entirely. So the binary is
  probed for "Universal Ctags" and this provider reports itself unavailable otherwise --
  a wrong answer here would be a silently empty definition list.
* **No extents.** ctags reports where a definition starts and not where it ends, so
  :data:`~relore.code.api.EXTENTS` is not declared and the tier above must not assume it.

Rule 4 says a provider is a pure function of ``(path, bytes)``, and this one honours that
by writing the bytes it was handed to a temporary file with the same suffix -- ctags needs
a path to guess a language, and reading the *caller's* file instead would make the answer
depend on a tree this function was never given.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterable

from relore.code.api import DEFS, Definition, Reference

#: Generous: ctags is fast, and a hung subprocess would hang `relore map`.
_TIMEOUT_SECONDS = 20


class CtagsProvider:
    name = "ctags"
    #: Everything, because this runs only when no first-class provider claimed the file.
    patterns = ("*",)
    capabilities = frozenset({DEFS})

    def available(self) -> bool:
        binary = shutil.which("ctags")
        if not binary:
            return False
        try:
            out = subprocess.run(  # noqa: S603 -- a fixed argv, no shell
                [binary, "--version"], capture_output=True, text=True, timeout=_TIMEOUT_SECONDS
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return "Universal Ctags" in out

    def defs(self, path: str, source: bytes) -> Iterable[Definition]:
        binary = shutil.which("ctags")
        if not binary:
            return []
        suffix = os.path.splitext(path)[1] or ".txt"
        with tempfile.TemporaryDirectory() as directory:
            scratch = os.path.join(directory, f"source{suffix}")
            with open(scratch, "wb") as handle:
                handle.write(source)
            try:
                result = subprocess.run(  # noqa: S603 -- a fixed argv, no shell
                    [binary, "--output-format=json", "--fields=+nKzS", "-f", "-", scratch],
                    capture_output=True,
                    text=True,
                    timeout=_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.SubprocessError):
                return []
        return list(self._parse(result.stdout))

    def refs(self, path: str, source: bytes) -> Iterable[Reference]:
        """Not offered. ``relore refs`` on a ctags-only language says so and exits 0 --
        section 9's rule 2: degradation is per tier, not all-or-nothing."""
        return []

    def _parse(self, stdout: str) -> Iterable[Definition]:
        for raw in stdout.splitlines():
            try:
                tag = json.loads(raw)
            except ValueError:
                continue
            if tag.get("_type") != "tag" or not tag.get("name"):
                continue
            name = str(tag["name"])
            scope = tag.get("scope")
            yield Definition(
                # Rule 1: the provider builds the whole string. ctags reports the scope
                # already joined in the language's own spelling, so use it verbatim rather
                # than re-joining it with a separator core would have to choose.
                qualname=f"{scope}.{name}" if scope else name,
                name=name,
                # ctags' own kind word, passed through as free text: section 9 forbids a
                # `symbol_type` vocabulary fixed in core, because that is how the next
                # language ends up misrepresented.
                kind=str(tag.get("kind") or "definition"),
                start_line=int(tag.get("line") or 0),
                end_line=None,  # ctags reports no end; EXTENTS is not declared
                parent=str(scope) if scope else None,
            )
