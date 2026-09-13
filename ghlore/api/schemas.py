"""Request and response shapes, versioned from the first commit (section 7).

The moment a second consumer exists, renaming a field breaks a stranger -- so the fields
of section 7 are all present now, including the ones whose *effect* is a later milestone.
``kind`` is the clearest case: it selects section 6's decay regime and section 6.2's trust
floor, neither of which exists yet, and it is accepted and echoed anyway because adding it
later would be a breaking change to a shipped API for no reason.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field

from ghlore.search.queries import (
    HUMAN_TRUST,
    MACHINE_TRUST,
    MAX_HITS,
    QUERY_KINDS,
    SORTS,
    Hit,
    InflightView,
    ThreadView,
    WhyView,
)


class SearchRequest(BaseModel):
    """``POST /api/v1/search``.

    ``limit`` is clamped, not validated: a client asking for 500 gets 10 (section 6). The
    signal filters read the tables section 5.3's extraction pass fills.
    """

    query: str = ""
    kind: str | None = Field(default=None, description=f"one of {list(QUERY_KINDS)}")
    trust: str | None = Field(
        default=None,
        description=(
            f"raise the floor to one of {list(HUMAN_TRUST)}, or {MACHINE_TRUST!r} to ask the "
            "separate question of what the deployment's own bots claimed. The server decides "
            "the default and a request can never lower it"
        ),
    )
    repos: list[str] = Field(
        default=[],
        description=(
            "narrow to these repositories. It can only ever *subtract* from the token's "
            "scope (section 11): a name the token cannot see is dropped, not granted, so "
            "asking for one returns nothing rather than an error. Empty means every "
            "repository already in scope"
        ),
    )
    files: list[str] = []
    symbols: list[str] = []
    errors: list[str] = []
    tests: list[str] = []
    labels: list[str] = []
    since: dt.datetime | None = None
    limit: int = MAX_HITS
    sort: str = Field(
        default="relevance",
        description=(
            f"one of {list(SORTS)}. Ordering only -- it reorders the same hits relevance "
            "would have chosen, and never changes which ones they are"
        ),
    )
    compact: bool = Field(
        default=False, description="shorter snippets, no score internals, for a tight budget"
    )
    render: bool = Field(
        default=False,
        description=(
            "also return the exact text the CLI would print, envelope included. What "
            "section 8's 'view as the model sees it' displays; off by default because an "
            "agent reading the JSON would be paying for the same content twice"
        ),
    )
    presentation: bool = Field(
        default=False,
        description=(
            "render for a person at a TTY: the same facts plus the backend tag and the "
            "flags worth trying next. The CLI sends it when stdout is a terminal (#13)"
        ),
    )
    expand: bool = Field(
        default=True,
        description=(
            "section 6's query expansion: fan the call out into error, test, symbol, file "
            "and free-text legs and merge them. On by default because the plain search "
            "ANDs every content term, which is what makes a pasted traceback match "
            "nothing. Turn it off to ask exactly one question"
        ),
    )


class LabelRequest(BaseModel):
    """``POST /api/v1/label`` -- section 8's relevance judgement, section 10's ground truth."""

    query: str
    repo: str
    number: int
    source_type: str
    verdict: str = Field(description="relevant | not_relevant | decisive")
    kind: str | None = None
    #: The signal filters the judged search actually carried. A label is only
    #: interpretable next to the whole query, and two examples can share the text and
    #: differ only in their `--file` -- folding the labels back into section 10's
    #: evaluation set has no way to tell them apart without this.
    filters: dict[str, list[str]] = {}
    note: str = ""


def hit_json(hit: Hit, *, compact: bool = False) -> dict[str, Any]:
    """One hit, as served.

    ``trust`` and ``age`` are always present and always rendered: age changes what a model
    concludes, authority changes it more (section 6, section 6.2). ``compact`` drops the
    score and its breakdown, which only a person tuning weights has any use for.
    """
    out: dict[str, Any] = {
        "repo": hit.repo,
        "number": hit.number,
        "type": hit.thread_type,
        "title": hit.title,
        "source_type": hit.source_type,
        "url": hit.url,
        "author": hit.author,
        "trust": hit.trust,
        "age": hit.age,
        "snippet": hit.snippet,
        # Which comment, and which piece of it (huggingface/ghlore#18). `url` is not an
        # identity: a chunk of a comment carries the comment's URL, so two hits from one
        # 9,827-character comment were indistinguishable from two people agreeing.
        # `document_id` is stable across rebuilds -- it is GitHub's id plus the chunk --
        # where the row's primary key is not.
        "document_id": f"{hit.source_type}:{hit.source_id}:{hit.chunk_index}",
        "source_id": hit.source_id,
        "chunk_index": hit.chunk_index,
    }
    # Absent rather than 1 where nobody counted: corpus-wide search does not pay for the
    # aggregate, and "1 of 1" would be a claim it cannot make.
    if hit.passages:
        out["passages"] = hit.passages
    if not compact:
        out["score"] = round(hit.score, 6)
        out["breakdown"] = {k: round(v, 6) for k, v in hit.breakdown.items()}
    return out


def inflight_json(view: InflightView) -> dict[str, Any]:
    """``GET /api/v1/inflight/{n}`` -- what claims to close this thread.

    ``links_indexed`` is here for the reason every count in this module is: an index with
    no relationship rows answers every question with an empty list, and an agent cannot
    tell that from "nobody is working on this" -- which are opposite instructions.
    """
    return {
        "repo": view.repo,
        "number": view.number,
        "claims": [
            {
                "repo": claim.repo,
                "number": claim.number,
                "type": claim.thread_type,
                "title": claim.title,
                "url": claim.url,
                "author": claim.author,
                "state": claim.state,
                "draft": claim.draft,
                "merged": claim.merged,
                "state_reason": claim.state_reason,
                "closed_by": claim.closed_by,
                "review_decision": claim.review_decision,
                "age": claim.age,
                "relationship": claim.relationship,
            }
            for claim in view.claims
        ],
        "claims_returned": len(view.claims),
        "claims_total": view.total,
        "links_indexed": view.links_indexed,
    }


def why_json(view: WhyView, *, blame: Any) -> dict[str, Any]:
    """``GET /api/v1/why`` -- the commit, the pull request, and what was said on the line.

    ``number: null`` and an empty ``anchored`` are different answers: no pull request could
    be resolved for the commit, versus one was and nobody reviewed this line.
    """
    return {
        "repo": view.repo,
        "path": view.path,
        "line": view.line,
        "blame": {
            "sha": blame.sha,
            "author": blame.author,
            "summary": blame.summary,
            "text": blame.text,
        },
        "number": view.number,
        "resolved_by": view.resolved_by,
        "thread": thread_json(view.thread) if view.thread else None,
        "anchored": [dict(comment) for comment in view.anchored],
    }


def _stamp(moment: dt.datetime | None) -> str | None:
    """A freshness mark a reader can compare with a GitHub timestamp, to the second.

    ``Z`` rather than an offset, and no microseconds: this is read next to the ages on the
    same page, and the six digits it would otherwise carry are noise in a line whose whole
    job is to be glanced at.
    """
    if moment is None:
        return None
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def thread_json(view: ThreadView, *, compact: bool = False) -> dict[str, Any]:
    return {
        "repo": view.repo,
        "number": view.number,
        "type": view.thread_type,
        "title": view.title,
        "url": view.url,
        "author": view.author,
        "state": view.state,
        "age": view.age,
        "labels": list(view.labels),
        # Events, not prose (issue #22).
        "state_reason": view.state_reason,
        "closed_by": view.closed_by,
        "merged": view.merged,
        "review_decision": view.review_decision,
        "review_decision_by": list(view.review_decision_by),
        "requested_reviewers": list(view.requested_reviewers),
        "body": view.body,
        # A cap a caller cannot see is a cap a caller reads as the whole document. `full`
        # serves the rest; these two say whether there is a rest.
        "body_chars": view.body_chars,
        "body_truncated": view.body_truncated,
        # Two keys, never one. Presence in `files_changed` says the thread changed the
        # path; presence in `files_mentioned` says somebody typed it, which is not the
        # same claim and was indistinguishable while both shared an array
        # (huggingface/ghlore#17).
        "files_changed": list(view.files_changed),
        # Also the diff, and also not part of the collected page: an inline review comment
        # can only hang on a changed file, so this recovers paths past the 100-row cap.
        "files_anchored": list(view.files_anchored),
        "files_mentioned": list(view.files_mentioned),
        # The denominator of the changed-file list, and `len(files_changed)` is its
        # numerator -- they are the same quantity, which the old bare count was not. A
        # truncated list must never be able to answer a membership question:
        # `files_collected < files_total` means a path that is absent may still have been
        # touched.
        "files_total": view.files_total,
        "files_collected": view.files_collected,
        "links": [dict(link) for link in view.links],
        # Named so the cap is visible in the response rather than inferred from a short
        # list: a caller that cannot tell truncation from a quiet thread will read ten
        # comments as the whole argument.
        "comments": [hit_json(hit, compact=compact) for hit in view.comments],
        "comments_returned": len(view.comments),
        # Comments, not documents: a long comment is several documents and counting rows
        # called one comment two (huggingface/ghlore#18). Every comment the thread has,
        # including the ones the trust floor withheld -- `comments_total` minus
        # `comments_machine_suppressed` is what the cap and the sampling compose with, and
        # a total computed post-filter said `0 of 0` about a thread we hold a comment for
        # (huggingface/ghlore#28).
        "comments_total": view.total_documents + view.machine_suppressed,
        # The gap, named rather than left to be inferred from a short list. Machine
        # authors are excluded and not down-weighted (section 6.2), so this is a filter a
        # reader has to know fired before concluding a thread is quiet.
        "comments_machine_suppressed": view.machine_suppressed,
        # When this response's thread was last rebuilt from GitHub. Per document, because
        # `status`'s per-source high-water rows cannot be composed into an answer about
        # one thread and two field runs composed them wrongly in opposite directions
        # (huggingface/ghlore#32). A string, not a datetime: `render` takes plain JSON
        # shapes and must read the same on both sides of the wire.
        "indexed_at": _stamp(view.indexed_at),
        # HOW those comments were chosen. A positional sample and a ranked top ten look
        # identical on the page, and the first five and last five of a 97-comment thread
        # were read as its ten best (huggingface/ghlore#16).
        "selection": view.selection,
        # A focus orders the comments and never selects them, so the count that matters is
        # how many carried every term: zero next to ten returned comments says "your
        # question matched nothing, this is the thread in order" rather than "nothing here".
        "focus": view.focus,
        "focus_matched": view.focus_matched,
    }
