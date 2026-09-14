"""Definitions, and the one operation the whole lens exists for.

``enclosing_symbol`` is what turns ``foo.py:412`` into ``Gemma3Model.forward``, and it is
the highest-value enrichment in section 1's table: use-cases section 8a #10 is the
strongest index-only case in the evidence base, and its query is literally *file/symbol ->
prior maintainer comments*. Bucketing an inline review comment by its enclosing function
instead of by a line number is what makes it survive drift within a file, and therefore
what makes a four-year-old review comment retrievable at all.

Both callers are here (section 1): ``relored`` points this at a tree read at a document's
own commit; ``relore defs`` points it at the working tree the caller is sitting in. Same
code, because it is a pure function of bytes either way.
"""

from __future__ import annotations

from relore.code.api import EXTENTS, Definition, Enclosing
from relore.code.registry import provider_for


def definitions(path: str, source: bytes) -> list[Definition]:
    """Every definition in one file, in source order.

    Raises :class:`~relore.code.api.MissingParser` when nothing can read the file, so the
    caller can print an install hint rather than an empty list that looks like an answer.
    """
    return list(provider_for(path).defs(path, source))


def enclosing_symbol(path: str, source: bytes, line: int) -> Enclosing | None:
    """What line ``line`` is inside, or ``None`` if it is inside nothing.

    Two tiers, and the difference is reported rather than hidden (section 9's rule 2):

    * with ``extents``, the **innermost** definition whose range contains the line. Innermost
      matters: a line in a method is inside both the method and its class, and the class is
      not the answer anyone wanted.
    * without them, the nearest **preceding** definition, flagged ``exact=False``. That is
      usually right and occasionally names the function above the one you meant -- which is
      why a caller has to be able to tell, since the alternative is quoting the wrong symbol
      with full confidence.
    """
    provider = provider_for(path)
    found = list(provider.defs(path, source))
    if not found:
        return None

    if EXTENTS in provider.capabilities:
        containing = [
            d for d in found if d.end_line is not None and d.start_line <= line <= d.end_line
        ]
        if not containing:
            return None
        innermost = max(containing, key=lambda d: d.start_line)
        return Enclosing(innermost.qualname, innermost.kind, innermost.start_line, exact=True)

    preceding = [d for d in found if d.start_line <= line]
    if not preceding:
        return None
    nearest = max(preceding, key=lambda d: d.start_line)
    return Enclosing(nearest.qualname, nearest.kind, nearest.start_line, exact=False)
