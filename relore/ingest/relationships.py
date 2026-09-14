"""Section 5.3's relationship edge: which thread a pull request claims to close.

One question, asked constantly and expensively answered wrong: **is somebody already
fixing this?** Duplicating work that is in review wastes the run and spends a maintainer's
review time on a redundant pull request, and it is entirely preventable from data the
index already holds. The evidence that it was *not* preventable before is in the field
report that produced this module: an agent read `huggingface/transformers#48630`, found two
comments, neither linking a fix, and was about to write a patch -- while
`huggingface/transformers#48672`, eight hours old, was open with `Fixes #48630` in its
body. It was found by accident, through a symbol search.

So the edge to build first is the explicit one, and not similarity. The titles in that
example share almost no vocabulary (*"GPTNeoXJapanese crashes for any `rotary_pct != 1.0`"*
against *"fix: respect partial_rotary_factor in GPTNeoXJapaneseRotaryEmbedding"*), so a
ranking over titles would have missed it; `Fixes #48630` is one hop through a relationship
the data model already declares.

**Two sources, and the cheap one is the one that matters.** GitHub's own
``closingIssuesReferences`` is definitive, and it arrives with the per-PR GraphQL pass --
which visits *merged* pull requests only (section 3). An in-flight pull request is by
definition unmerged, so for the query this exists for the only available source is the
body text. Both are read; the body is what makes the answer timely.

**Pull requests only.** The keyword closes an issue when GitHub sees it in a pull request;
in an issue body "fixes #12" is prose. Extracting it there would manufacture edges that
GitHub itself does not draw.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

#: GitHub's own closing keywords, and its own punctuation tolerance: `Fixes #12`,
#: `fixed: #12`, `Closes  #12`. A bare `#12` is deliberately *not* an edge -- most
#: cross-references in a pull request body are context, not a claim to resolve.
_CLOSES = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s*#(\d+)",
    re.IGNORECASE,
)

#: The one relationship this pass writes. The vocabulary is open -- `duplicates`,
#: `reverts`, `mentions` -- but a relationship nothing queries is a row nothing reads.
CLOSES = "closes"


def extract_links(
    thread_type: str,
    number: int,
    documents: Iterable[Mapping[str, Any]],
    detail: Mapping[str, Any] | None = None,
    *,
    repo: str = "",
) -> list[dict[str, Any]]:
    """The link claims of one thread, as signal rows.

    ``documents`` are the rows about to be written, for the reason section 5.3 gives for
    every other signal: extraction reads the *documents*, never ``raw_objects``, so a
    secret kept out of ``body_text`` cannot reappear through a signal table. Only the
    body's chunks are read -- a keyword in a comment is somebody talking about a fix, not
    the pull request's own claim -- and every chunk of it, because a long body's closing
    line can land in the second one.

    ``detail`` is the per-PR GraphQL node. Its ``closingIssuesReferences`` can point at
    another repository, so an entry is used only when it names this one: a bare number
    from a foreign repository would resolve against ours and claim the wrong thread.
    """
    if thread_type != "pr":
        return []

    targets: dict[int, None] = {}
    for document in documents:
        if document.get("source_type") != "body":
            continue
        for match in _CLOSES.finditer(str(document.get("body_markdown") or "")):
            targets.setdefault(int(match.group(1)), None)

    for node in ((detail or {}).get("closingIssuesReferences") or {}).get("nodes") or []:
        owner = str(((node or {}).get("repository") or {}).get("nameWithOwner") or "")
        if owner and repo and owner.lower() != repo.lower():
            continue
        if node and node.get("number") is not None:
            targets.setdefault(int(node["number"]), None)

    # A thread cannot close itself, and a pull request body quoting its own number is
    # common enough ("supersedes #48672") to be worth refusing rather than storing.
    return [
        {"relationship": CLOSES, "target_number": target, "target_thread_id": None}
        for target in sorted(targets)
        if target != number
    ]
