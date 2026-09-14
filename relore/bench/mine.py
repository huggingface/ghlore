"""Mine section 10's three slices out of an index.

Mining produces **candidates**, not ground truth: every example comes out with
``judged_by=None`` and a ``source`` block holding the evidence it was surfaced from, so
whoever labels it can open the comment rather than take the miner's word for it. The
judgement is the part that cannot be mechanized, and pretending otherwise is how a
benchmark ends up measuring its own miner.

What *is* mechanical is different per slice, and section 10 says so:

* **rationale** -- a maintainer replying inside a review thread is the evidence. The bot
  subset is the exact query shape (a *proposed change*, answered), but there are only 18
  of them in the sample; the human subset gives 4,071 and needs the leak declaration
  below.
* **failure** -- section 5.3's extracted errors and test ids already group threads that
  share a failure. A group of two-to-eight threads is a question with an answer in it; a
  group of two hundred is a generic exception.
* **precedent** -- "do it like #12345" is a common review comment and is greppable, which
  section 10 calls partly mechanical ground truth. The reference has to resolve to a
  thread that is actually indexed, or the example is unanswerable by construction.

**The leak declaration.** In the rationale slice the query is drawn from the finding, and
for a *human* finding that text is itself a retrievable document in the target thread --
so every lexical system, `relore` and both baselines alike, can hit the answer without
retrieving anything. ``source["leaks"]`` records it per candidate; a bot finding is
excluded from reads (section 6.2) and does not leak. The runner reports the two subsets
apart, because a recall figure pooled over both describes neither.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, func, select

from relore.bench.dataset import Corpus, Example, Relevant
from relore.ingest.extract import is_symbol
from relore.store import schema as s

#: How strongly a reply reads as "a maintainer settling a question" rather than as the
#: next turn of a conversation. Mining without this ranks 4,071 replies at random and the
#: labeller reads two hundred to find twenty-five.
RATIONALE_CUES: tuple[tuple[str, int], ...] = (
    (r"\bon purpose\b", 4),
    (r"\bintentional(ly)?\b", 4),
    (r"\bby design\b", 4),
    (r"\bdeliberate(ly)?\b", 4),
    (r"\bthat'?s expected\b", 3),
    (r"\bwe (don'?t|do not|never|always) \w+", 3),
    (r"\b(belongs?|should live|should go) (in|to|under)\b", 3),
    (r"\bnot? (need|needed|necessary)\b", 2),
    (r"\b(backward|backwards)[- ]compat", 2),
    (r"\b(legacy|historical|historic reasons?)\b", 2),
    (r"\bfor now\b", 1),
    (r"\bbecause\b", 1),
)

#: A precedent reference. The verb matters: a bare ``#123`` is as likely to be a bug
#: report as a precedent, and "fixes #123" is a *link* (milestone 4), not a precedent --
#: it names the thing being closed, not a prior change to imitate.
PRECEDENT_RE = re.compile(
    r"\b(?:like|same (?:as|issue as|way as)|as in|as we did in|similar to|done in|was done in|"
    r"following|follow[- ]?up (?:to|of)|refer to|as per|port of|mirrors?|introduced in|"
    r"landed in|added in|cf\.?|see)\s+#(\d{2,6})\b",
    re.IGNORECASE,
)

#: A backticked span. Whether it is a *symbol* is section 5.3's question, not a second
#: opinion: :func:`relore.ingest.extract.is_symbol` already rejects `None`, bare prose and
#: `webapp.py`, and a miner that answered it differently would build queries the index was
#: never asked to match.
_CODE_SPAN = re.compile(r"`{1,3}([^`\n]+)`{1,3}")

_MIN_ANSWER_CHARS = 60
_MAX_ANSWER_CHARS = 2000


@dataclass(frozen=True)
class MineStats:
    considered: int = 0
    kept: int = 0
    reason: dict[str, int] | None = None


def corpus_of(conn: Connection, repo: str) -> tuple[Corpus, ...]:
    """The indexed window, read out of ``repo_sample``.

    A repository with no sample row was backfilled in full and gets one entry with no
    floor -- section 10's rule is "restrict every baseline by the same qualifier", and
    the honest qualifier for a full history is none.
    """
    rows = (
        conn.execute(
            select(s.repo_sample)
            .where(s.repo_sample.c.repo == repo)
            .order_by(s.repo_sample.c.thread_type)
        )
        .mappings()
        .all()
    )
    counts = dict(
        conn.execute(
            select(s.threads.c.thread_type, func.count())
            .where(s.threads.c.repo == repo)
            .group_by(s.threads.c.thread_type)
        ).all()
    )
    docs = dict(
        conn.execute(
            select(s.threads.c.thread_type, func.count())
            .select_from(s.documents.join(s.threads, s.threads.c.id == s.documents.c.thread_id))
            .where(s.threads.c.repo == repo)
            .group_by(s.threads.c.thread_type)
        ).all()
    )
    if not rows:
        return (
            Corpus(
                repo=repo,
                thread_type="all",
                since=None,
                selector="full history",
                threads=sum(counts.values()),
                documents=sum(docs.values()),
            ),
        )
    out = []
    for row in rows:
        kind = row["thread_type"]
        out.append(
            Corpus(
                repo=repo,
                thread_type=kind,
                since=row["indexed_from"].date().isoformat(),
                selector=row["selector"],
                threads=counts.get(kind, 0),
                documents=docs.get(kind, 0),
            )
        )
    return tuple(out)


# -- rationale --------------------------------------------------------------


def mine_rationale(conn: Connection, repo: str, *, limit: int = 60) -> list[Example]:
    """Review replies from an author with write access, paired with what they answer."""
    rows = (
        conn.execute(
            select(
                s.documents.c.source_id,
                s.documents.c.thread_id,
                s.documents.c.author,
                s.documents.c.author_is_bot,
                s.documents.c.trust,
                s.documents.c.url,
                s.documents.c.body_text,
                s.documents.c.metadata,
                s.threads.c.github_number,
                s.threads.c.title,
            )
            .select_from(s.documents.join(s.threads, s.threads.c.id == s.documents.c.thread_id))
            .where(
                s.threads.c.repo == repo,
                s.documents.c.source_type == "review_comment",
                s.documents.c.chunk_index == 0,
            )
        )
        .mappings()
        .all()
    )

    by_id = {r["source_id"]: r for r in rows}
    scored: list[tuple[int, Example]] = []
    for reply in rows:
        if reply["trust"] != "authoritative" or reply["author_is_bot"]:
            continue
        parent_id = _meta(reply).get("in_reply_to_id")
        if parent_id is None:
            continue
        finding = by_id.get(str(parent_id))
        if finding is None or finding["author"] == reply["author"]:
            continue
        answer = reply["body_text"].strip()
        if not (_MIN_ANSWER_CHARS <= len(answer) <= _MAX_ANSWER_CHARS):
            continue
        score, cues = _cue_score(answer)
        terms = _query_terms(finding["body_text"], answer)
        path = _meta(finding).get("path") or _meta(reply).get("path")
        if not terms and not path:
            continue
        # A bot finding is the exact shape section 10 asks for and is worth reading
        # first; it is also the only one that cannot leak.
        bot = bool(finding["author_is_bot"]) or finding["trust"] == "machine"
        scored.append(
            (
                # A candidate with no distinctive term is a file filter and nothing else:
                # still a real query shape, but it asks a much weaker question, so it
                # sorts below anything that named something.
                score + (5 if bot else 0) - (0 if terms else 3),
                Example(
                    id=f"rationale:{repo}#{reply['github_number']}:{reply['source_id']}",
                    kind="rationale",
                    query=" ".join(terms),
                    files=(path,) if path else (),
                    relevant=(
                        Relevant(
                            repo=repo,
                            number=int(reply["github_number"]),
                            why=_clip(answer, 300),
                        ),
                    ),
                    note="",
                    source={
                        "shape": "bot_finding" if bot else "human_finding",
                        "leaks": not bot,
                        "cues": cues,
                        "finding_author": finding["author"],
                        "finding": _clip(finding["body_text"].strip(), 400),
                        "finding_url": finding["url"],
                        "answer_author": reply["author"],
                        "answer_url": reply["url"],
                        "title": reply["title"],
                    },
                ),
            )
        )
    return _top(scored, limit)


# -- failure ----------------------------------------------------------------


def mine_failure(
    conn: Connection, repo: str, *, limit: int = 80, min_threads: int = 2, max_threads: int = 8
) -> list[Example]:
    """Errors and test ids that more than one thread carries.

    The window is the point. One thread carrying an error is not a retrieval question --
    there is nothing to rank. Two hundred threads carrying ``list index out of range``
    is not one either: no system can be right, and scoring it measures the corpus.
    """
    out: list[tuple[int, Example]] = []
    out += _group_examples(
        conn,
        repo,
        table=s.thread_errors,
        keys=(s.thread_errors.c.exception_type, s.thread_errors.c.message_norm),
        min_threads=min_threads,
        max_threads=max_threads,
        build=_failure_from_error,
    )
    out += _group_examples(
        conn,
        repo,
        table=s.thread_tests,
        keys=(s.thread_tests.c.test_id,),
        min_threads=min_threads,
        max_threads=max_threads,
        build=_failure_from_test,
    )
    return _top(out, limit)


def _group_examples(conn, repo, *, table, keys, min_threads, max_threads, build):
    counted = (
        select(*keys, func.count(func.distinct(table.c.thread_id)).label("n"))
        .select_from(table.join(s.threads, s.threads.c.id == table.c.thread_id))
        .where(s.threads.c.repo == repo)
        .group_by(*keys)
        .having(func.count(func.distinct(table.c.thread_id)).between(min_threads, max_threads))
    )
    groups = conn.execute(counted).mappings().all()
    made: list[tuple[int, Example]] = []
    for group in groups:
        where = [k == group[k.name] for k in keys]
        threads = (
            conn.execute(
                select(s.threads.c.github_number, s.threads.c.title, s.threads.c.thread_type)
                .select_from(table.join(s.threads, s.threads.c.id == table.c.thread_id))
                .where(s.threads.c.repo == repo, *where)
                .distinct()
                .order_by(s.threads.c.github_number)
            )
            .mappings()
            .all()
        )
        example = build(repo, group, threads)
        if example is not None:
            # Fewer carriers is a sharper question, so rank the small groups first.
            made.append((max_threads - len(threads), example))
    return made


def _failure_from_error(repo: str, group, threads) -> Example | None:
    exc, msg = group["exception_type"], group["message_norm"]
    if not msg or len(msg) < 12:
        return None
    raw = f"{exc}: {msg}" if exc else msg
    return Example(
        id=f"failure:{repo}:err:{abs(hash(raw)) % 10**10}",
        kind="failure",
        query=" ".join(_distinctive(msg)),
        errors=(raw,),
        relevant=tuple(
            Relevant(repo=repo, number=int(t["github_number"]), why=_clip(t["title"], 200))
            for t in threads
        ),
        source={
            "shape": "error",
            "exception_type": exc,
            "message_norm": msg,
            "carriers": len(threads),
            "leaks": False,
        },
    )


def _failure_from_test(repo: str, group, threads) -> Example | None:
    test_id = group["test_id"]
    if "::" not in test_id:
        return None
    return Example(
        id=f"failure:{repo}:test:{abs(hash(test_id)) % 10**10}",
        kind="failure",
        query="",
        tests=(test_id,),
        relevant=tuple(
            Relevant(repo=repo, number=int(t["github_number"]), why=_clip(t["title"], 200))
            for t in threads
        ),
        source={"shape": "test", "test_id": test_id, "carriers": len(threads), "leaks": False},
    )


# -- precedent --------------------------------------------------------------


def mine_precedent(conn: Connection, repo: str, *, limit: int = 60) -> list[Example]:
    """ "do it like #12345" -- the described change, and the thread it points at."""
    indexed = {
        n
        for (n,) in conn.execute(
            select(s.threads.c.github_number).where(s.threads.c.repo == repo)
        ).all()
    }
    rows = (
        conn.execute(
            select(
                s.documents.c.source_id,
                s.documents.c.author,
                s.documents.c.url,
                s.documents.c.body_text,
                s.threads.c.github_number,
                s.threads.c.title,
            )
            .select_from(s.documents.join(s.threads, s.threads.c.id == s.documents.c.thread_id))
            .where(
                s.threads.c.repo == repo,
                s.documents.c.chunk_index == 0,
                s.documents.c.trust != "machine",
            )
        )
        .mappings()
        .all()
    )

    scored: list[tuple[int, Example]] = []
    for row in rows:
        text = row["body_text"]
        targets = {
            int(m.group(1))
            for m in PRECEDENT_RE.finditer(text)
            if int(m.group(1)) in indexed and int(m.group(1)) != row["github_number"]
        }
        if not targets:
            continue
        # A precedent query is "a described change", and the description a caller has
        # in hand is their own title. Fall back to it when the comment names the prior
        # thread without backticking anything.
        terms = _query_terms(text, "") or _title_terms(row["title"])
        if not terms:
            continue
        scored.append(
            (
                len(targets),
                Example(
                    id=f"precedent:{repo}#{row['github_number']}:{row['source_id']}",
                    kind="precedent",
                    query=" ".join(terms),
                    relevant=tuple(
                        Relevant(repo=repo, number=n, why="named as precedent")
                        for n in sorted(targets)
                    ),
                    source={
                        "shape": "named_precedent",
                        "leaks": False,
                        "author": row["author"],
                        "asked_in": int(row["github_number"]),
                        "excerpt": _clip(text.strip(), 400),
                        "url": row["url"],
                        "title": row["title"],
                    },
                ),
            )
        )
    return _top(scored, limit)


# -- shared -----------------------------------------------------------------


MINERS = {"rationale": mine_rationale, "failure": mine_failure, "precedent": mine_precedent}


def _meta(row) -> dict[str, Any]:
    meta = row["metadata"]
    return meta if isinstance(meta, dict) else {}


def _cue_score(text: str) -> tuple[int, list[str]]:
    score, hit = 0, []
    for pattern, weight in RATIONALE_CUES:
        if re.search(pattern, text, re.IGNORECASE):
            score += weight
            hit.append(pattern)
    return score, hit


def _query_terms(primary: str, secondary: str, *, want: int = 2) -> tuple[str, ...]:
    """Two or three distinctive terms, the way the CLI's own help says to search.

    A query is an AND of every content term, so a sentence matches nothing; backticked
    identifiers are what a person would actually have typed instead.
    """
    seen: list[str] = []
    for text in (primary, secondary):
        for span in _CODE_SPAN.findall(text or ""):
            span = span.strip()
            if not is_symbol(span):
                continue
            term = span.rpartition(".")[2] if "." in span else span
            if len(term) < 4 or term.lower() in {t.lower() for t in seen}:
                continue
            seen.append(term)
            if len(seen) >= want:
                return tuple(seen)
    return tuple(seen)


def _title_terms(title: str, *, want: int = 2) -> tuple[str, ...]:
    """Identifier-shaped words out of a thread title, for when nothing was backticked."""
    out: list[str] = []
    for word in re.findall(r"[A-Za-z_][\w.]{3,}", title or ""):
        if not is_symbol(word) or word.lower() in {w.lower() for w in out}:
            continue
        out.append(word.rpartition(".")[2] if "." in word else word)
        if len(out) >= want:
            break
    return tuple(out)


def _distinctive(message: str, *, want: int = 3) -> tuple[str, ...]:
    """The words of an error a person would keep when they cut it down to a query."""
    words = [w for w in re.findall(r"[a-z_][a-z_]{3,}", message.lower()) if w not in _STOP]
    out: list[str] = []
    for word in words:
        if word not in out:
            out.append(word)
        if len(out) >= want:
            break
    return tuple(out)


_STOP = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "for",
        "with",
        "from",
        "this",
        "that",
        "have",
        "has",
        "must",
        "been",
        "will",
        "your",
        "you",
        "not",
        "are",
        "was",
        "were",
        "only",
        "when",
        "where",
        "what",
        "which",
        "while",
        "into",
        "over",
        "under",
        "than",
        "then",
        "them",
        "they",
    ]
)


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _top(scored: Iterable[tuple[int, Example]], limit: int) -> list[Example]:
    """Rank, deduplicate, cut.

    Two candidates asking the same question of the same threads are one example however
    many comments they were mined from -- three replies under one review thread produced
    three identical rationale rows on the first run, which would have weighted that thread
    triple in the recall average.
    """
    ordered = sorted(scored, key=lambda pair: (-pair[0], pair[1].id))
    kept: list[Example] = []
    seen: set[tuple] = set()
    for _, example in ordered:
        key = (
            example.kind,
            example.query.lower(),
            example.files,
            example.errors,
            example.tests,
            tuple(sorted((r.repo, r.number) for r in example.relevant)),
        )
        if key in seen:
            continue
        seen.add(key)
        kept.append(example)
        if len(kept) >= limit:
            break
    return kept
