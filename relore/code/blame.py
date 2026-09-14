"""`git blame` for one line, from the daemon's working clone (issue #9).

Blame is the thing a cheap clone cannot give you: `--depth 200` is plenty to *read* a file
and useless for attributing a line, so an agent minimising clone cost lands in a hole no
local implementation can dig out of. The daemon's clone is complete for exactly this
reason -- the whole history is addressable, and locally.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

TIMEOUT = 120


@dataclass(frozen=True)
class BlameLine:
    sha: str
    author: str
    author_time: str
    summary: str
    text: str


@dataclass(frozen=True)
class BlameCommit:
    """One revision in a line's history, oldest last -- `git log -L`, not `git blame`."""

    sha: str
    date: str
    summary: str


def blame_line(root: str, path: str, line: int) -> BlameLine | None:
    """Who last touched ``line``, or ``None`` when the file or line is not there."""
    # A query does not reach the network, and this is what makes that a property of the
    # call rather than a claim about `CLONE_ARGS`: against a clone that is partial anyway,
    # blame fails here instead of hanging on a fetch to github.com.
    done = subprocess.run(
        ["git", "blame", "-L", f"{line},{line}", "--porcelain", "--", path],
        cwd=root,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )
    if done.returncode != 0 or not done.stdout:
        return None

    lines = done.stdout.splitlines()
    fields: dict[str, str] = {}
    for entry in lines[1:]:
        if entry.startswith("\t"):
            fields["text"] = entry[1:]
            break
        key, _, value = entry.partition(" ")
        fields.setdefault(key, value)
    return BlameLine(
        sha=lines[0].split(" ", 1)[0],
        author=fields.get("author", ""),
        author_time=fields.get("author-time", ""),
        summary=fields.get("summary", ""),
        text=fields.get("text", ""),
    )


def line_history(
    root: str, path: str, start: int, end: int, *, limit: int = 8
) -> tuple[BlameCommit, ...]:
    """Every revision that touched ``start..end``, newest first (huggingface/relore#57).

    ``blame`` answers *who touched this line last*, which on a file that has been
    reformatted, moved or renamed since is a cosmetic pull request sitting in front of the
    one that decided anything. Measured on `transformers`: blame of
    ``generation/utils.py:2301`` names #43121, a later refactor, while the behaviour was
    argued in #37866 -- and an agent handed only the first spent eleven further calls
    looking for the second.

    ``git log -L`` follows the range through the file's history, so a line that moved is
    still tracked. What it does **not** follow is a line that was deleted and reintroduced
    elsewhere: the chain ends where this range's history ends, and a caller wanting the
    whole story of a *string* wants ``-S`` and a term to pickaxe on, which is a judgement
    this cannot make for them. So the chain is offered as history, never as provenance.
    """
    if end < start:
        start, end = end, start
    done = subprocess.run(
        [
            "git",
            "log",
            f"-L{start},{end}:{path}",
            "--format=@@%H%x09%ad%x09%s",
            "--date=short",
            "-s",
            f"--max-count={limit}",
        ],
        cwd=root,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )
    if done.returncode != 0 or not done.stdout:
        return ()

    out: list[BlameCommit] = []
    for entry in done.stdout.splitlines():
        if not entry.startswith("@@"):
            continue
        sha, _, rest = entry[2:].partition("\t")
        date, _, summary = rest.partition("\t")
        out.append(BlameCommit(sha=sha, date=date, summary=summary))
    return tuple(out)


@dataclass(frozen=True)
class Origin:
    """The line's *string* history, and which word bought it (huggingface/relore#57, #65).

    ``term`` is the word pickaxed on, ``commits`` what `git log -S` returned for it oldest
    last, and ``considered`` every candidate with the depth it reached -- because the
    choice is a judgement and a page that does not show its working cannot be argued with.
    """

    term: str = ""
    commits: tuple[BlameCommit, ...] = ()
    considered: tuple[tuple[str, int], ...] = ()


#: Words that are never the distinguishing thing on a line. Short and unapologetically
#: rough: the *ranking* is what picks the term (see :func:`origin_history`), and this only
#: stops the obvious waste of a pickaxe on `self` or `return`. A word wrongly left in costs
#: one `git log -S` on one path, which is milliseconds.
STOPWORDS = frozenset(
    {
        # keywords and the one identifier every method carries
        "self",
        "this",
        "class",
        "def",
        "function",
        "return",
        "import",
        "export",
        "const",
        "let",
        "var",
        "static",
        "void",
        "public",
        "private",
        "new",
        "delete",
        "try",
        "catch",
        "except",
        "finally",
        "raise",
        "throw",
        "pass",
        "else",
        "elif",
        "while",
        "true",
        "false",
        "null",
        "none",
        "int",
        "str",
        "bool",
        # prose that carries no narrowing power in a comment
        "the",
        "and",
        "for",
        "not",
        "with",
        "from",
        "use",
        "using",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "can",
        "will",
        "should",
        "would",
        "must",
        "may",
        "might",
        "its",
        "it",
        "is",
        "in",
        "of",
        "to",
        "we",
        "you",
        "they",
        "them",
        "their",
        "there",
        "then",
        "than",
        "when",
        "where",
        "which",
        "who",
        "what",
        "why",
        "how",
    }
)

#: How many candidate words get a pickaxe. The line has a handful of words worth trying and
#: each is a `git log -S` over one path -- five on `transformers`' largest generation file
#: measured under a second in total. More would be a cost with no candidate left to spend
#: it on.
MAX_CANDIDATES = 5

#: An identifier, or a word long enough to be worth pickaxing. Two characters is `id` and
#: matches everything.
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

#: A comment opener in the languages this index actually holds. A line's *reason* is
#: usually in the comment above it -- `# If we use FA and a static cache, we cannot compile
#: with fullgraph` is where `fullgraph` comes from -- so the block is the line plus the
#: comment attached to it.
_COMMENT = ("#", "//", "/*", "*", "--", ";")


def origin_history(
    root: str,
    path: str,
    line: int,
    *,
    known: Sequence[BlameCommit] = (),
    limit: int = 8,
) -> Origin:
    """Where the *behaviour* on this line came from, when its line history cannot say.

    This is the judgement :func:`line_history` declines to make, and the reason it has to
    be made somewhere. ``git log -L`` follows a *range* through the file's history, so it
    tracks a line that moved and loses one that was rewritten -- and rewritten is the
    common case. Measured on the line this verb was written for,
    ``generation/utils.py:2301``: the `-L` chain is eight later pull requests and does not
    contain #37866, which is the one that argued the behaviour. Every agent handed only
    that chain went and ran ``git log -S`` by hand, which is the call this saves.

    **The term is chosen by what it finds, not by how it looks**, and both obvious rules
    are wrong on their own. *Rarest word on the line* picks ``is_flash_attention_requested``
    here -- 2 occurrences in the file -- and a pickaxe on it returns exactly the one commit
    ``blame`` already named, because that name arrived with the refactor. *Deepest history*
    picks ``compile``, ``cannot`` and ``cache``, which are simply common: they fill any cap,
    tie on it, and the tie is then broken by nothing.

    So the rule is both, in order: **the most distinctive word that actually reaches further
    back than the line already does.** Candidates are tried rarest first; one whose history
    fills the probe is unbounded and skipped; one whose oldest commit is no older than the
    revision chain's has added nothing and is skipped; the first survivor wins. On the
    motivating line ``is_flash_attention_requested`` is tried first and rejected -- its one
    commit is *newer* than the chain's oldest -- and ``fullgraph`` is taken: 7 commits back
    to 2024-03-06, containing both #37866 and #40137, the two pull requests three separate
    runs had to find by hand with exactly this command. ``compile`` and the rest are never
    reached, so the usual call costs two pickaxes rather than five.

    ``known`` is the `-L` chain, whose oldest commit is the bar every candidate has to
    clear. Passing nothing means every candidate clears it, which is right for a caller
    that has no chain to compare against.
    """
    known_commits = tuple(known)
    source = _read(root, path)
    if source is None:
        return Origin()
    lines = source.splitlines()
    if not 1 <= line <= len(lines):
        return Origin()

    counts: dict[str, int] = {}
    for word in _WORD.findall(source):
        counts[word] = counts.get(word, 0) + 1
    block = _block(lines, line)
    ranked = sorted(
        # Rarity first, then length: both are "how much does this word narrow the file",
        # and neither is the decision -- the pickaxe below is.
        dict.fromkeys(w for w in _WORD.findall(block) if w.lower() not in STOPWORDS),
        key=lambda word: (counts.get(word, 0), -len(word)),
    )[:MAX_CANDIDATES]

    seen = {commit.sha[:12] for commit in known_commits}
    # The bar: the oldest commit the caller already has. `None` means there is no chain to
    # beat, so every candidate clears it -- which is right for a caller with nothing to
    # compare against, and is not the same as a bar nothing can clear.
    floor = min((commit.date for commit in known_commits), default=None)
    considered: list[tuple[str, int]] = []
    for word in ranked:
        # One more than the page, so that "filled the page" and "this is all of it" are
        # distinguishable. Without the extra row they are the same number.
        commits = _pickaxe(root, path, word, limit + 1)
        considered.append((word, len(commits)))
        if not commits or len(commits) > limit:
            continue
        if {c.sha[:12] for c in commits} <= seen or (
            floor is not None and commits[-1].date >= floor
        ):
            # It stops no earlier than the line's own history does, so it is another way of
            # saying what the caller already has. Reject rather than serve it twice.
            continue
        return Origin(term=word, commits=commits, considered=tuple(considered))
    # Nothing beat the revision chain. Saying so is the point: an empty `origin` next to a
    # full `history` means the line history *is* the whole story, which is a real answer
    # and the one this verb used to give by omission.
    return Origin(considered=tuple(considered))


def _block(lines: list[str], line: int) -> str:
    """The line, plus the comment block attached above it."""
    block = [lines[line - 1]]
    index = line - 2
    while index >= 0 and lines[index].strip().startswith(_COMMENT):
        block.append(lines[index])
        index -= 1
    return " ".join(block)


def _read(root: str, path: str) -> str | None:
    try:
        with open(os.path.join(root, path), encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _pickaxe(root: str, path: str, term: str, limit: int) -> tuple[BlameCommit, ...]:
    """``git log -S<term> -- <path>``: the commits that changed how often ``term`` appears.

    ``-S`` and not ``-G``: the question is where the behaviour was introduced and removed,
    not every commit whose diff mentions the word. ``--`` and the path, so this is bounded
    by one file's history rather than the repository's.
    """
    done = subprocess.run(
        [
            "git",
            "log",
            f"-S{term}",
            "--format=@@%H%x09%ad%x09%s",
            "--date=short",
            f"--max-count={limit}",
            "--",
            path,
        ],
        cwd=root,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )
    if done.returncode != 0 or not done.stdout:
        return ()
    out: list[BlameCommit] = []
    for entry in done.stdout.splitlines():
        if not entry.startswith("@@"):
            continue
        sha, _, rest = entry[2:].partition("\t")
        date, _, summary = rest.partition("\t")
        out.append(BlameCommit(sha=sha, date=date, summary=summary))
    return tuple(out)
