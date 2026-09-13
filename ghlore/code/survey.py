"""Questions about a whole tree rather than one file (issue #7): `grep` and `copies`.

`copies` is the one this corpus is shaped for. `transformers` duplicates model code on
purpose, so "the same function, copied into 38 files, which copies diverge" is the native
shape of a bug there -- and `huggingface/transformers#48630` was exactly that: one model
diverging from the other 37. The answer is a grouping, not a list, because the question is
never "where is it" but "which ones are different".
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from fnmatch import fnmatch

from ghlore.code.api import Definition
from ghlore.code.defs import definitions
from ghlore.code.registry import claimed
from ghlore.code.walk import all_files, read, source_files

MAX_MATCHES = 500
MAX_COPIES = 200


@dataclass(frozen=True)
class GrepHit:
    path: str
    line: int
    text: str


@dataclass
class GrepResult:
    pattern: str
    hits: list[GrepHit] = field(default_factory=list)
    files_searched: int = 0
    truncated: bool = False


@dataclass(frozen=True)
class Copy:
    path: str
    qualname: str
    start_line: int
    end_line: int | None
    body_hash: str
    lines: int


@dataclass
class CopiesResult:
    symbol: str
    copies: list[Copy] = field(default_factory=list)
    truncated: bool = False

    @property
    def groups(self) -> dict[str, list[Copy]]:
        """Copies by body hash, largest group first: the majority shape, then the outliers."""
        out: dict[str, list[Copy]] = {}
        for copy in self.copies:
            out.setdefault(copy.body_hash, []).append(copy)
        return dict(sorted(out.items(), key=lambda kv: (-len(kv[1]), kv[0])))


def grep(root: str, pattern: str, *, path_glob: str | None = None) -> GrepResult:
    """A regular expression over every file in the tree, not only the parseable ones.

    The audit question -- "which files have this shape of code" -- reaches YAML, docs and
    templates, so the set here is the walker's, not the providers'.
    """
    regex = re.compile(pattern)
    result = GrepResult(pattern=pattern)
    for path in all_files(root):
        relative = _relative(root, path)
        if path_glob and not fnmatch(relative, path_glob):
            continue
        source = read(path)
        if source is None:
            continue
        result.files_searched += 1
        for number, line in enumerate(source.decode("utf-8", "replace").splitlines(), start=1):
            if regex.search(line):
                result.hits.append(GrepHit(path=relative, line=number, text=line.strip()[:300]))
                if len(result.hits) >= MAX_MATCHES:
                    result.truncated = True
                    return result
    return result


def copies(root: str, symbol: str) -> CopiesResult:
    """Every definition of ``symbol``, grouped by whether the bodies are identical.

    The hash is over the body with leading whitespace stripped per line, so a copy that
    differs only by indentation -- a function lifted into a class -- groups with its
    original rather than reading as a divergence.
    """
    result = CopiesResult(symbol=symbol)
    for path, definition, source in _definitions_named(root, symbol):
        body = _body(source, definition)
        result.copies.append(
            Copy(
                path=_relative(root, path),
                qualname=definition.qualname,
                start_line=definition.start_line,
                end_line=definition.end_line,
                body_hash=_hash(body),
                lines=len(body),
            )
        )
        if len(result.copies) >= MAX_COPIES:
            result.truncated = True
            break
    result.copies.sort(key=lambda copy: copy.path)
    return result


@dataclass(frozen=True)
class SymbolBody:
    path: str
    definition: Definition
    body: str
    #: How many definitions of this name the tree holds. Never 1 by assumption: in a
    #: repository that duplicates model code, serving the first of 38 as *the* body is a
    #: wrong answer a caller cannot see, so the count travels with it and `copies` groups
    #: them.
    total: int


def symbol_body(root: str, qualname: str) -> SymbolBody | None:
    """The source of one definition, so a caller can read it without cloning (#7 item 2)."""
    name = qualname.rsplit(".", 1)[-1]
    found = [
        (_relative(root, path), definition, "\n".join(_body(source, definition)))
        for path, definition, source in _definitions_named(root, name)
    ]
    if not found:
        return None
    exact = [item for item in found if item[1].qualname == qualname]
    path, definition, body = sorted(exact or found)[0]
    return SymbolBody(path=path, definition=definition, body=body, total=len(found))


def _definitions_named(root: str, name: str) -> Iterator[tuple[str, Definition, list[str]]]:
    for path in source_files(root):
        if not claimed(path):
            continue
        raw = read(path)
        if raw is None:
            continue
        source = raw.decode("utf-8", "replace").splitlines()
        for definition in definitions(path, raw):
            if definition.name == name or definition.qualname.endswith(f".{name}"):
                yield path, definition, source


def _body(source: list[str], definition: Definition) -> list[str]:
    end = definition.end_line or definition.start_line
    return source[definition.start_line - 1 : end]


def _hash(body: list[str]) -> str:
    stripped = "\n".join(line.strip() for line in body if line.strip())
    return hashlib.sha256(stripped.encode()).hexdigest()[:12]


def _relative(root: str, path: str) -> str:
    prefix = root.rstrip("/") + "/"
    return path[len(prefix) :] if path.startswith(prefix) else path
