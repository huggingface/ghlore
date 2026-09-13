"""Turning an API response into the text a model reads. One implementation, two callers.

The CLI prints this, and section 8's "view as the model sees it" shows the *same* string --
including the untrusted envelope and its delimiter scrubbing -- so that a formatting or
injection bug is caught by a person reading it rather than by an agent meeting it mid-task.
Two renderers would drift, and the one that drifted would be the one nobody was looking at.

**With no MCP server, stdout is an API** (#13), so the piped form is a contract:
``presentation=False`` -- what a caller gets when stdout is not a TTY -- prints facts only,
in a documented line grammar (``docs/cli.md``). ``presentation=True`` adds advice and
diagnostics for the person at the terminal: the backend tag, `--full`, `--focus`,
`ghlored derive`. **Facts are never presentation**, so every count, cap and caveat is in
both forms; only the suggestions move.

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


def render_search(
    payload: dict[str, Any], *, compact: bool = False, presentation: bool = False
) -> str:
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
        + (f"   [{backend.get('name', '?')}/{backend.get('ranking', '?')}]" if presentation else "")
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
    return envelope("\n".join(body).rstrip(), compact=compact)


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


def render_thread(
    payload: dict[str, Any], *, compact: bool = False, presentation: bool = False
) -> str:
    """One thread, with the cap stated rather than implied.

    A caller that cannot tell truncation from a quiet thread will read ten comments as the
    whole argument, so the count is on the page -- and so is every other reason a comment
    is not on it. The tier filter used to be the silent one: a thread whose only comment
    is machine-authored rendered as ``0 of 0``, which is the sentence for an untouched
    thread (huggingface/ghlore#28).

    The head line also carries when this thread was last rebuilt from GitHub
    (huggingface/ghlore#32). ``status`` has that number per ingestion *source*, which no
    reader can compose into an answer about one document -- and two runs have now drawn
    opposite wrong conclusions trying to.
    """
    thread = payload.get("thread") or {}
    lines = [
        f"{thread.get('repo')}#{thread.get('number')} {thread.get('type')}  "
        f"{thread.get('state')}  {thread.get('age')}",
        quote(thread.get("title", "")),
    ]
    if thread.get("author"):
        lines.append(f"opened by @{thread['author']}")
    lines += _event_lines(thread)
    if thread.get("labels"):
        lines.append(f"labels: {', '.join(thread['labels'])}")
    lines += _file_lines(thread)
    if thread.get("links"):
        lines.append("links: " + ", ".join(_link(link) for link in thread["links"]))
    lines += ["", quote(thread.get("body", ""))]
    if thread.get("body_truncated"):
        lines.append(
            f"(body truncated: {len(thread.get('body') or '')} of "
            f"{thread.get('body_chars')} characters."
            + (
                " `--full` serves the rest, which on an issue template is where the "
                "reproduction starts."
                if presentation
                else ""
            )
            + ")"
        )
    lines.append("")

    comments = thread.get("comments") or []
    returned = thread.get("comments_returned", len(comments))
    # `comments_total` is every comment on the thread. `visible` is how many of them this
    # trust floor admits, and it -- never the total -- is what the cap and the sampling
    # compose with: the machine tier is a separate question, not a page we ran out of room
    # for (huggingface/ghlore#28).
    total = thread.get("comments_total", 0)
    suppressed = int(thread.get("comments_machine_suppressed") or 0)
    visible = max(total - suppressed, returned)
    focus, matched = thread.get("focus") or "", thread.get("focus_matched")
    clauses = []
    if suppressed:
        # Filtered is not empty. `0 of 0` on a thread we hold one bot comment for asserts
        # the thread is untouched, which is the one thing it did not mean -- and it is the
        # failure signature #11 was opened to eliminate (huggingface/ghlore#28).
        clauses.append(
            f"{suppressed} machine-tier suppressed"
            + (
                " (`ghlore search --trust machine` asks what the bots claimed)"
                if presentation
                else ""
            )
        )
    if focus:
        clause = f"best first for {focus!r}"
        if matched is not None:
            clause += f" ({matched} of {visible} carry every term)"
        clauses.append(clause)
    elif thread.get("selection") == "ends+middle" and visible > returned:
        # The selection, named. Ten comments under a bare count read as the ten best, and
        # an agent that believes it has read the best ten stops (huggingface/ghlore#16).
        clauses.append("SAMPLED not ranked: the first and last few and a spread of the middle")
    # Per-response freshness (huggingface/ghlore#32). `status` reports it per ingestion
    # source, and two runs have now composed those rows into opposite wrong conclusions
    # about one thread -- once too trusting, once not enough. The only question a reader
    # asks before acting is whether *these* comments are current, so the answer travels
    # with them instead of being assembled from a strip.
    if thread.get("indexed_at"):
        clauses.append(f"current to {thread['indexed_at']}")
    head = ", ".join([f"-- {returned} of {total} comments", *clauses])
    lines.append(head + " --")
    for index, comment in enumerate(comments, start=1):
        lines += _hit_lines(index, comment, note=_passage_note(comment, comments))
    if visible > returned:
        lines.append(
            f"({visible - returned} not shown: a thread is never returnable in full."
            + (
                ' `--focus "<what you care about>"` ranks all of them.'
                if presentation and not focus
                else ""
            )
            + ")"
        )
    return envelope("\n".join(lines).rstrip(), source=thread.get("url"), compact=compact)


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


def _claim_state(claim: dict[str, Any]) -> list[str]:
    """``closed (duplicate) by @maintainer`` rather than ``closed`` (issue #22).

    Two claimants both reading ``closed`` is the state this verb is worst at: withdrawn by
    its author means review the survivor, ruled a duplicate means read the triage.
    """
    if claim.get("merged"):
        return [claim.get("state") or "", "merged"]
    parts = [claim.get("state") or "", "draft" if claim.get("draft") else ""]
    if claim.get("state") == "closed":
        reason, closer = claim.get("state_reason"), claim.get("closed_by")
        if reason and reason != "completed":
            parts.append(f"({reason.replace('_', ' ')})")
        if closer:
            parts.append("by its author" if closer == claim.get("author") else f"by @{closer}")
    elif claim.get("review_decision"):
        parts.append(f"({claim['review_decision'].replace('_', ' ')})")
    return parts


def _event_lines(thread: dict[str, Any]) -> list[str]:
    """How the thread got to its state, and what the reviewers settled (issue #22).

    `closed` is the same word for *withdrawn by its author* and *ruled a duplicate*, which
    imply opposite next actions; and a review decision is the only thing that tells
    ``0 of 0 comments`` apart from *approved without typing*.
    """
    lines = []
    if thread.get("merged"):
        lines.append("merged")
    elif thread.get("state") == "closed":
        reason, closer = thread.get("state_reason"), thread.get("closed_by")
        parts = ["closed"]
        if reason and reason != "completed":
            parts.append(f"as {reason.replace('_', ' ')}")
        if closer:
            parts.append("by its author" if closer == thread.get("author") else f"by @{closer}")
        if len(parts) > 1:
            lines.append(" ".join(parts))

    decision, by = thread.get("review_decision"), thread.get("review_decision_by") or []
    requested = thread.get("requested_reviewers") or []
    if decision:
        who = ", ".join(f"@{login}" for login in by)
        lines.append(f"review: {decision.replace('_', ' ')}" + (f" by {who}" if who else ""))
    elif requested:
        who = ", ".join(f"@{login}" for login in requested)
        lines.append(f"review: requested from {who}, no verdict yet")
    elif thread.get("type") == "pr" and thread.get("state") == "open":
        lines.append("review: nobody has approved or blocked it")
    return lines


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


def render_why(payload: dict[str, Any], *, presentation: bool = False) -> str:
    """Why one line is the way it is (issue #9).

    Blame's commit first, then the pull request that carried it, then the part blame
    cannot give: what reviewers said *on this line* while it was being written.
    """
    blame = payload.get("blame") or {}
    thread = payload.get("thread") or {}
    lines = [
        f"{payload.get('repo')} {payload.get('path')}:{payload.get('line')}",
        quote(blame.get("text", "")),
        "",
        f"last changed in {blame.get('sha', '')[:12]} by {blame.get('author', 'someone')}",
        quote(blame.get("summary", "")),
    ]
    if payload.get("number") is None:
        lines.append(
            "\nno pull request in this index carries that commit, so the argument behind "
            "it is not retrievable here. It may predate the index, or have reached the "
            "branch outside a pull request."
        )
        return envelope("\n".join(lines).rstrip())

    guessed = (
        " (from the commit subject, not a staged commit row)"
        if (payload.get("resolved_by") == "summary")
        else ""
    )
    lines += [
        "",
        f"{thread.get('repo')}#{payload['number']} {thread.get('state', '')}{guessed}",
        quote(thread.get("title", "")),
    ]
    if thread.get("url"):
        lines.append(thread["url"])
    if thread.get("links"):
        lines.append("links: " + ", ".join(_link(link) for link in thread["links"]))

    anchored = payload.get("anchored") or []
    lines += ["", f"-- {len(anchored)} review comment(s) on this line while it was written --"]
    for index, comment in enumerate(anchored, start=1):
        tier = TRUST_LABEL.get(str(comment.get("trust")), str(comment.get("trust")))
        head = f"{index}. [{tier}]  {comment.get('age')}"
        if comment.get("author"):
            head += f"  @{comment['author']}"
        if comment.get("line"):
            head += f"  (line {comment['line']})"
        lines += [head, quote(str(comment.get("text", "")))]
        if comment.get("url"):
            lines.append(f"   {comment['url']}")
        lines.append("")
    if not anchored and presentation:
        lines.append(
            "(nobody reviewed this line. `ghlore thread` reads the rest of the discussion.)"
        )
    return envelope("\n".join(lines).rstrip(), source=thread.get("url"))


def render_inflight(payload: dict[str, Any], *, presentation: bool = False) -> str:
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
                "answer yet"
                + (": re-derive it with `ghlored derive` to fill them" if presentation else "")
                + ".)"
            )
        return envelope("\n".join(lines))

    claim_word = "thread claims" if total == 1 else "threads claim"
    lines = [f"{total} {claim_word} to close {subject}", ""]
    for index, claim in enumerate(claims, start=1):
        state = " ".join(part for part in _claim_state(claim) if part)
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
