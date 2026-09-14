"""The pickaxe behind `why`'s origin chain (huggingface/relore#57).

`git log -L` follows a *range*, so it tracks a line that moved and loses a block that was
**rewritten** -- and rewritten is the common case. Measured on the line this verb was
written for, `transformers` `generation/utils.py:2301`: the whole eight-commit revision
chain postdates #37866, the pull request that argued the behaviour, and three separate
agent runs left the verb and ran `git log -S` by hand to find it.

What is asserted here is the **choice of term**, because that is the judgement, and both
obvious rules for it are wrong on their own. The synthetic history below reproduces the
real shape: a rare name that arrived with a later refactor, and a word carried by the whole
history that the refactor left only in a comment.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from relore.code.blame import MAX_CANDIDATES, STOPWORDS, line_history, origin_history


def _git(root, *argv: str, when: str | None = None) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else None
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True, env=env)


def _commit(root, path, body: str, message: str, when: str) -> None:
    """Dated apart on purpose: the rule compares how far back a candidate reaches, and
    commits stamped in the same second are the one shape that cannot show it."""
    (root / path).write_text(body)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", message, when=when)


#: Enough unchanging lines that a commit editing the top of the file does not touch the
#: bottom. Without it `git log -L` walks back through every commit of a short file and the
#: shape being tested -- a block whose own history begins at the rewrite -- cannot exist.
FILLER = "\n".join(f"CONST_{n} = {n}" for n in range(60))


@pytest.fixture
def repo(tmp_path):
    """Three commits, dated apart, shaped like the real case.

    ``fullgraph`` is introduced at the top of the file and argued there. The last commit
    deletes it and writes a new check at the **bottom**, carrying the word only in a
    comment -- which is exactly where the real line's ``fullgraph`` lives. So the new
    line's own revision history begins at that rewrite and the argument is unreachable
    from it, while ``git log -S fullgraph`` still reaches the commit that introduced it.
    """
    root = tmp_path / "tree"
    (root / "src").mkdir(parents=True)
    _git(tmp_path, "init", "-q", "tree")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")

    _commit(
        root,
        "src/mod.py",
        f"def a(cfg):\n    fullgraph = False\n    return fullgraph\n\n{FILLER}\n",
        "the origin: introduce fullgraph (#1)",
        "2024-03-06T00:00:00Z",
    )
    _commit(
        root,
        "src/mod.py",
        f"def a(cfg):\n    fullgraph = True\n    return fullgraph\n\n{FILLER}\n",
        "argue the default (#2)",
        "2025-05-22T00:00:00Z",
    )
    _commit(
        root,
        "src/mod.py",
        f"def a(cfg):\n    return True\n\n{FILLER}\n\n\ndef b(cfg):\n"
        "    # we cannot compile with fullgraph\n"
        "    if is_flash_attention_requested(cfg):\n"
        "        return False\n",
        "rename the check (#3)",
        "2026-01-15T00:00:00Z",
    )
    return str(root)


def _line_of(root: str, needle: str) -> int:
    with open(f"{root}/src/mod.py") as handle:
        for number, text in enumerate(handle, start=1):
            if needle in text:
                return number
    raise AssertionError(needle)


def _asked(root: str):
    """How the endpoint asks: the line, and the revision chain it already has.

    That chain is one commit long here, and that is the whole shape being tested: the line
    did not exist before the rewrite, so `git log -L` over it starts and ends there."""
    line = _line_of(root, "is_flash_attention_requested")
    return line, line_history(root, "src/mod.py", line, line)


def test_the_rarest_word_on_the_line_is_not_the_answer(repo) -> None:
    """`is_flash_attention_requested` is the rarest word here and arrived *with* the last
    commit, so its pickaxe returns only what blame already said. Choosing by rarity alone
    picks it, which is why rarity only orders the candidates."""
    line, known = _asked(repo)

    found = origin_history(repo, "src/mod.py", line, known=known)

    assert found.term == "fullgraph"
    assert dict(found.considered)["is_flash_attention_requested"] == 1


def test_the_chosen_chain_reaches_what_the_line_history_cannot(repo) -> None:
    """The whole point. The block was rewritten, so the revision chain cannot reach the
    commit that introduced the behaviour; the word's history can."""
    line, known = _asked(repo)

    found = origin_history(repo, "src/mod.py", line, known=known)

    assert [c.summary for c in known] == ["rename the check (#3)"], (
        "the chain starts at the rewrite"
    )
    assert [c.summary for c in found.commits][-1] == "the origin: introduce fullgraph (#1)"


def test_the_comment_above_the_line_is_part_of_the_question(repo) -> None:
    """A line's *reason* is usually in the comment attached to it. On the real line the code
    says only `is_flash_attention_requested`; `fullgraph` is in the comment above it, and
    without reading that comment the right term is not a candidate at all."""
    line, known = _asked(repo)

    assert "fullgraph" in dict(origin_history(repo, "src/mod.py", line, known=known).considered)


def test_a_word_whose_history_fills_the_page_is_discarded(tmp_path) -> None:
    """A word appearing in more of this file's history than a page can hold is not what
    distinguishes one line of it -- and at the cap every such word ties on the same number,
    so the tie would be broken by nothing. Measured on the real line: `compile`, `cannot`
    and `cache` all returned the cap.

    The probe asks for one more than the page, so that "filled it" and "this is all of it"
    are different numbers."""
    root = tmp_path / "tree"
    root.mkdir()
    _git(tmp_path, "init", "-q", "tree")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    # One more occurrence each time: `-S` reports commits that changed how *often* the
    # string appears, so a line whose value changes but whose word does not is one commit.
    for n in range(1, 6):
        body = "# widely_edited\n" + "widely_edited = True\n" * n
        _commit(root, "mod.py", body, f"bump {n}", f"202{n}-01-01T00:00:00Z")

    found = origin_history(root.as_posix(), "mod.py", 1, limit=2)

    assert found.commits == ()
    assert dict(found.considered)["widely_edited"] > 2, "it was tried, and it was unbounded"


def test_a_chain_that_reaches_no_further_back_is_not_served_twice(repo) -> None:
    """An empty `origin` next to a full `history` is a real answer -- the line history *is*
    the whole story -- and it has to be distinguishable from "the pickaxe was not run"."""
    line, known = _asked(repo)
    already = origin_history(repo, "src/mod.py", line, known=known)

    found = origin_history(repo, "src/mod.py", line, known=already.commits)

    assert already.commits, "there was something to find the first time"
    assert found.commits == ()
    assert found.term == ""
    assert found.considered, "it still says what it tried"


def test_at_most_a_handful_of_words_are_pickaxed(repo) -> None:
    """Each candidate is a `git log -S` over one path. Five is under a second on
    `transformers`' largest generation file -- and the real call stops at two, because the
    first survivor wins and there is nothing left to spend more on."""
    line, known = _asked(repo)

    assert len(origin_history(repo, "src/mod.py", line, known=known).considered) <= MAX_CANDIDATES


def test_keywords_are_not_candidates() -> None:
    """`self` and `return` narrow nothing, and a pickaxe on either is a wasted subprocess."""
    assert {"self", "return", "class", "the"} <= STOPWORDS


def test_a_path_that_is_not_there_is_empty_not_an_error(repo) -> None:
    """14.4(b) in miniature: the lens may be absent and the answer must still be the old
    answer rather than a failure."""
    assert origin_history(repo, "src/absent.py", 1).commits == ()
    assert origin_history(repo, "src/mod.py", 99999).commits == ()
