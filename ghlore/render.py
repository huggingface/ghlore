"""Turning an API response into the text a model reads. One implementation, two callers.

The CLI prints this, and section 8's "view as the model sees it" shows the *same* string --
including the untrusted envelope and its delimiter scrubbing -- so that a formatting or
injection bug is caught by a person reading it rather than by an agent meeting it mid-task.
Two renderers would drift, and the one that drifted would be the one nobody was looking at.

**Everything here takes plain dictionaries -- the JSON shapes of section 7 -- and imports
nothing but the standard library.** That is what lets it live on both sides of the
boundary: :mod:`ghlore.cli` may not reach a database driver or a web server (AGENTS.md
invariant 1), and importing :mod:`ghlore.search` would pull SQLAlchemy in through the
package's ``__init__``. Dicts in, text out.
"""

from __future__ import annotations

from typing import Any

from ghlore.security.untrusted import envelope, quote

#: What each tier is called in front of a snippet. Four tokens, and the difference between
#: a fact and someone's opinion (section 6.2).
TRUST_LABEL = {
    "authoritative": "authoritative",
    "reported": "contributor claim",
    "machine": "MACHINE — our own bot, not evidence",
}


def render_search(payload: dict[str, Any], *, compact: bool = False) -> str:
    """The result page, wrapped in the envelope.

    Empty is not an error: no hits renders as a sentence saying so, still enveloped, still
    exit 0.
    """
    query = payload.get("query") or {}
    hits = payload.get("hits") or []
    backend = payload.get("backend") or {}
    header = [
        f"{len(hits)} hit{'' if len(hits) == 1 else 's'}"
        + (f" for {query.get('text')!r}" if query.get("text") else "")
        + f"   [{backend.get('name', '?')}/{backend.get('ranking', '?')}]"
    ]
    floor = query.get("trust_floor") or []
    if floor and floor != ["reported", "authoritative"]:
        # A raised floor and a quiet corpus produce the same empty page, so say which.
        header.append(f"trust floor: {', '.join(floor)}")
    if not hits:
        header.append("nothing matched.")

    body = [*header, ""]
    for index, hit in enumerate(hits, start=1):
        body += _hit_lines(index, hit, note=_passage_note(hit, hits))
    return envelope("\n".join(body).rstrip())


def _hit_lines(index: int, hit: dict[str, Any], note: str = "") -> list[str]:
    """One hit. ``compact`` does not reach here: it shortens the snippet server-side and
    drops the score breakdown from the JSON, and the score is no longer rendered at all.

    ``note`` is for what the head line cannot imply -- today, that this hit is one passage
    of a longer comment rather than a comment of its own.
    """
    tier = TRUST_LABEL.get(str(hit.get("trust")), str(hit.get("trust")))
    head = (
        f"{index}. {hit.get('repo')}#{hit.get('number')} {hit.get('type')}  "
        f"[{tier}]  {hit.get('age')}  {hit.get('source_type')}"
    )
    if hit.get("author"):
        head += f"  @{hit['author']}"
    if note:
        head += f"  ({note})"
    # The two fields that are somebody else's words get marked as such; the head line
    # above -- tier, age, author, source type -- is ours (huggingface/ghlore#12).
    lines = [head]
    # Skipped when empty rather than printed blank: a comment carries its thread's title,
    # and a blank line still costs the caller a token.
    lines += [f"   {quote(text)}" for text in (hit.get("title"), hit.get("snippet")) if text]
    if hit.get("url"):
        lines.append(f"   {hit['url']}")
    # No score. The ranking is already expressed by the order, and `score 0.03009` above
    # `score 0.03125` supports no decision a caller can act on -- it is four tokens per
    # hit spent on a number whose scale is a property of the backend. It stays in `--json`
    # with its breakdown, for section 8's page and whoever is tuning the weights.
    lines.append("")
    return lines


def render_thread(payload: dict[str, Any], *, compact: bool = False) -> str:
    """One thread, with the cap stated rather than implied.

    A caller that cannot tell truncation from a quiet thread will read ten comments as the
    whole argument, so the count is on the page.
    """
    thread = payload.get("thread") or {}
    lines = [
        f"{thread.get('repo')}#{thread.get('number')} {thread.get('type')}  "
        f"{thread.get('state')}  {thread.get('age')}",
        quote(thread.get("title", "")),
    ]
    if thread.get("author"):
        lines.append(f"opened by @{thread['author']}")
    if thread.get("labels"):
        lines.append(f"labels: {', '.join(thread['labels'])}")
    lines += _file_lines(thread)
    if thread.get("links"):
        lines.append("links: " + ", ".join(_link(link) for link in thread["links"]))
    lines += ["", quote(thread.get("body", ""))]
    if thread.get("body_truncated"):
        lines.append(
            f"(body truncated: {len(thread.get('body') or '')} of "
            f"{thread.get('body_chars')} characters. `--full` serves the rest, which on an "
            "issue template is where the reproduction starts.)"
        )
    lines.append("")

    comments = thread.get("comments") or []
    returned, total = (
        thread.get("comments_returned", len(comments)),
        thread.get("comments_total", 0),
    )
    focus, matched = thread.get("focus") or "", thread.get("focus_matched")
    head = f"-- {returned} of {total} comments"
    if focus:
        head += f", best first for {focus!r}"
        if matched is not None:
            head += f" ({matched} of {total} carry every term)"
    elif thread.get("selection") == "ends+middle" and total > returned:
        # The selection, named. Ten comments under a bare count read as the ten best, and
        # an agent that believes it has read the best ten stops (huggingface/ghlore#16).
        head += ", SAMPLED not ranked: the first and last few and a spread of the middle"
    lines.append(head + " --")
    for index, comment in enumerate(comments, start=1):
        lines += _hit_lines(index, comment, note=_passage_note(comment, comments))
    if total > returned:
        lines.append(
            f"({total - returned} not shown: a thread is never returnable in full."
            + ("" if focus else ' `--focus "<what you care about>"` ranks all of them.')
            + ")"
        )
    return envelope("\n".join(lines).rstrip(), source=thread.get("url"))


def _passage_note(hit: dict[str, Any], page: list[dict[str, Any]]) -> str:
    """Whether this hit is a piece of a longer comment, and how to read the page if so.

    A 9,827-character comment is several indexed documents, and two of them came back as
    hits 1 and 2 with the same URL, author, age and tier -- which reads as two sources
    agreeing rather than one comment quoted twice (huggingface/ghlore#18). Both halves of
    that are worth saying: that the snippet is an excerpt of something longer, and that
    another slot on this page came from the same comment.
    """
    passages = int(hit.get("passages") or 0)
    index = int(hit.get("chunk_index") or 0)
    identity = (hit.get("url"), hit.get("source_id"))
    if not hit.get("source_id"):
        return ""
    same = sum(1 for other in page if (other.get("url"), other.get("source_id")) == identity)
    parts = []
    if passages > 1:
        parts.append(f"passage {index + 1} of {passages} in this comment")
    elif index:
        parts.append(f"passage {index + 1} in this comment")
    if same > 1:
        parts.append(f"{same} of its passages are on this page")
    return "; ".join(parts)


def _link(link: dict[str, Any]) -> str:
    """One relationship edge. ``indexed`` is on the page because an unresolved target is
    the normal case on a sampled index, and "we have not indexed #12" is a different fact
    from "#12 does not exist"."""
    suffix = "" if link.get("indexed", True) else " (not indexed)"
    return f"{link['relationship']} #{link['target']}{suffix}"


def _file_lines(thread: dict[str, Any]) -> list[str]:
    """The changed files and the mentioned ones, on separate lines, each with its meaning.

    A short list of *hits* is read as a weak positive; a *missing entry* is read as a
    negative fact. ``huggingface/transformers#39847`` is the case this exists for: 323
    changed files, 100 collected because the per-PR pass takes one page, and no
    ``gpt_neox`` entry among them. The pull request does touch ``gpt_neox_japanese``, so
    the absence would have exonerated the change that caused the bug being diagnosed.

    The inverse error is the reason for the split (huggingface/ghlore#17). Merged into one
    array, five prose-derived filenames sat among a hundred real paths: ``config.json``,
    which that pull request does not touch, answered "did it change this?" with yes. A
    truncated list must not answer a membership question silently in *either* direction,
    so the two provenances are two lines and each says what it is.
    """
    changed = thread.get("files_changed") or []
    anchored = thread.get("files_anchored") or []
    mentioned = thread.get("files_mentioned") or []
    total = thread.get("files_total")
    collected = thread.get("files_collected") or len(changed)
    if not changed and not anchored and not mentioned and total is None:
        return []

    lines = []
    if total is None:
        if changed:  # no `changed_files` count, yet a diff: an index predating migration 7
            lines.append(f"changed files: {len(changed)} (no total recorded for this thread)")
            lines.append("  " + ", ".join(changed))
    elif collected >= total:
        lines.append(f"changed files: {collected} of {total} — complete")
        lines.append("  " + ", ".join(changed))
    elif collected:
        lines.append(
            f"changed files: {collected} of {total} collected. TRUNCATED: a path that is "
            "absent here may still have been touched"
        )
        lines.append("  " + ", ".join(changed))
    else:
        lines.append(
            f"changed files: none collected of {total}, so this thread cannot answer "
            "whether it touched a path"
        )
    if anchored and lines:
        lines.append(
            f"  + {len(anchored)} more the diff must contain, from the files inline "
            "review comments are anchored to (not part of the collected page)"
        )
        lines.append("  " + ", ".join(anchored))
    elif anchored:
        # No changed-file line to continue from -- an issue, or a pull request whose
        # per-PR pass has not run. The anchors are still the diff, and saying so as a
        # fragment under nothing would read as a footnote to a list that is not there.
        lines.append(
            f"changed files: not collected for this thread, but {len(anchored)} are known "
            "from the files inline review comments are anchored to (a comment can only "
            "hang on a changed file). Absence is still not evidence"
        )
        lines.append("  " + ", ".join(anchored))
    if mentioned:
        lines.append(
            f"mentioned in the discussion: {len(mentioned)} — named by somebody, NOT the "
            "diff. A bare filename here is not evidence the thread changed it"
        )
        lines.append("  " + ", ".join(mentioned))
    return lines


def render_inflight(payload: dict[str, Any]) -> str:
    """What already claims to close a thread.

    Empty is the answer this verb exists to give, so it has to be a sentence rather than a
    blank page -- and it has to be distinguishable from *unanswerable*: an index with no
    relationship rows at all says so, because "nobody is working on this" and "this index
    cannot tell you" imply opposite next actions.
    """
    claims = payload.get("claims") or []
    subject = f"{payload.get('repo')}#{payload.get('number')}"
    total = payload.get("claims_total", len(claims))
    if not claims:
        lines = [f"nothing in the index claims to close {subject}."]
        if not payload.get("links_indexed"):
            lines.append(
                "(and this repository has no relationship rows at all, so that is not an "
                "answer yet: re-derive it with `ghlored derive` to fill them.)"
            )
        return envelope("\n".join(lines))

    claim_word = "thread claims" if total == 1 else "threads claim"
    lines = [f"{total} {claim_word} to close {subject}", ""]
    for index, claim in enumerate(claims, start=1):
        state = " ".join(
            part
            for part in (
                claim.get("state"),
                "draft" if claim.get("draft") else "",
                "merged" if claim.get("merged") else "",
            )
            if part
        )
        head = (
            f"{index}. {claim.get('repo')}#{claim.get('number')} {claim.get('type')}  "
            f"{state}  {claim.get('age')}  {claim.get('relationship')}"
        )
        if claim.get("author"):
            head += f"  @{claim['author']}"
        lines.append(head)
        if claim.get("title"):
            lines.append(f"   {quote(claim['title'])}")
        if claim.get("url"):
            lines.append(f"   {claim['url']}")
        lines.append("")
    if total > len(claims):
        lines.append(f"({total - len(claims)} more, not shown.)")
    return envelope("\n".join(lines).rstrip())


def render_status(payload: dict[str, Any]) -> str:
    """No envelope: this is our own numbers, not retrieved text."""
    backend = payload.get("backend") or {}
    schema = payload.get("schema") or {}
    lines = [
        f"version   {payload.get('version')}",
        f"backend   {backend.get('name')} / {backend.get('ranking')}  "
        f"capabilities: {', '.join(backend.get('capabilities') or []) or 'none'}",
        f"schema    applied {schema.get('applied')}, pending {schema.get('pending')}",
        f"index     {payload.get('threads')} threads, {payload.get('documents')} documents, "
        f"{payload.get('raw_objects')} raw objects",
    ]
    for row in payload.get("passes") or []:
        lines.append(
            f"  {row['repo']} [{row['pass']}] high-water {row['high_water']} "
            f"last-ok {row['last_ok_at']}"
        )
    usage = payload.get("usage")
    if usage:
        lines.append(
            f"quota     {usage['per_minute']['used']}/{usage['per_minute']['limit']} per minute, "
            f"{usage['daily']['used']}/{usage['daily']['limit']} today"
        )
    return "\n".join(lines)
