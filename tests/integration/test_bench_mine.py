"""Mining section 10's candidates out of a real index, on both dialects.

What is asserted is what a labeller depends on: the right shapes are surfaced, the wrong
ones are not, nothing is counted twice, and every candidate arrives *unjudged* -- a miner
that shipped ground truth would be scoring its own heuristics.
"""

from __future__ import annotations

import pytest
from fake_github import FakeGitHub
from sqlalchemy import Engine

from relore.bench import mine
from relore.ingest.index_thread import index_thread

REPO = "owner/name"


@pytest.fixture
def fake() -> FakeGitHub:
    return FakeGitHub(REPO)


def _index(engine: Engine, fake: FakeGitHub, *numbers: int) -> None:
    with fake.client() as client:
        for number in numbers:
            index_thread(engine, client, fake.repo, number)


def _mine(engine: Engine, which, **kwargs):
    with engine.connect() as conn:
        return which(conn, REPO, **kwargs)


# -- rationale -------------------------------------------------------------


def test_a_maintainer_reply_to_a_finding_is_a_candidate(engine: Engine, fake: FakeGitHub) -> None:
    pr = fake.add_pr(1, title="Add the adapter")
    fake.add_review_comment(pr, 100, "why not subclass `ModelOutput` here?", author="bob")
    fake.add_review_comment(
        pr,
        101,
        "`ModelOutput` carries backwards-compatibility magic we do not need; this is "
        "deliberate and `load_adapter` relies on it.",
        author="owner",
        assoc="OWNER",
        in_reply_to=100,
    )
    _index(engine, fake, 1)

    found = _mine(engine, mine.mine_rationale)

    assert len(found) == 1
    assert found[0].kind == "rationale"
    assert found[0].relevant[0].number == 1
    assert found[0].files == ("src/mod.py",)
    assert "ModelOutput" in found[0].query
    assert found[0].judged_by is None


def test_a_reply_from_a_contributor_is_not_a_candidate(engine: Engine, fake: FakeGitHub) -> None:
    """Section 6.2: this slice asks whether somebody with write access already settled the
    question. A drive-by answer is the case the trust floor exists to exclude."""
    pr = fake.add_pr(1)
    fake.add_review_comment(pr, 100, "why not subclass `ModelOutput` here?", author="bob")
    fake.add_review_comment(
        pr,
        101,
        "I think this is deliberate, `ModelOutput` has magic we do not want here at all.",
        author="drive-by",
        assoc="NONE",
        in_reply_to=100,
    )
    _index(engine, fake, 1)

    assert _mine(engine, mine.mine_rationale) == []


def test_a_reply_to_oneself_is_not_a_finding_answered(engine: Engine, fake: FakeGitHub) -> None:
    pr = fake.add_pr(1)
    fake.add_review_comment(pr, 100, "`ModelOutput` here", author="owner", assoc="OWNER")
    fake.add_review_comment(
        pr,
        101,
        "actually, on reflection this is deliberate and `load_adapter` depends on it.",
        author="owner",
        assoc="OWNER",
        in_reply_to=100,
    )
    _index(engine, fake, 1)

    assert _mine(engine, mine.mine_rationale) == []


def test_a_bot_finding_is_ranked_first_and_declared_leak_free(
    engine: Engine, fake: FakeGitHub
) -> None:
    """Section 6.2 excludes machine documents from reads, so a bot's finding is not in the
    index the query is run against. A human finding *is* -- and every lexical system can
    then hit the answer without retrieving anything, which the candidate has to say."""
    pr = fake.add_pr(1)
    fake.add_review_comment(pr, 100, "`ModelOutput` mismatch here", author="bob")
    fake.add_review_comment(
        pr,
        101,
        "this is deliberate; `load_adapter` has relied on that shape since the start.",
        author="owner",
        assoc="OWNER",
        in_reply_to=100,
    )
    fake.add_review_comment(
        pr, 200, "`AttentionMask` is built twice in this path", author="copilot", bot=True
    )
    fake.add_review_comment(
        pr,
        201,
        "on purpose, `AttentionMask` is cheap and `build_mask` needs both.",
        author="owner",
        assoc="OWNER",
        in_reply_to=200,
    )
    _index(engine, fake, 1)

    found = _mine(engine, mine.mine_rationale)

    assert [ex.source["shape"] for ex in found] == ["bot_finding", "human_finding"]
    assert [ex.source["leaks"] for ex in found] == [False, True]


def test_the_same_question_is_not_mined_twice(engine: Engine, fake: FakeGitHub) -> None:
    """Three replies under one review thread produced three identical rows on the first
    run, which would have weighted that thread triple in the recall average."""
    pr = fake.add_pr(1)
    fake.add_review_comment(pr, 100, "`ModelOutput` here?", author="bob")
    for reply_id in (101, 102, 103):
        fake.add_review_comment(
            pr,
            reply_id,
            f"this is deliberate; `ModelOutput` has magic we do not need ({reply_id}).",
            author="owner",
            assoc="OWNER",
            in_reply_to=100,
        )
    _index(engine, fake, 1)

    assert len(_mine(engine, mine.mine_rationale)) == 1


def test_a_backticked_filename_is_not_used_as_a_query_term(
    engine: Engine, fake: FakeGitHub
) -> None:
    """Section 13.2 #5 settled this for the extractor; the miner must not hold a second
    opinion, or it builds queries the index was never asked to match. Left to itself the
    first run turned `finegrained_fp8.py` into the term "py"."""
    pr = fake.add_pr(1)
    fake.add_review_comment(pr, 100, "`finegrained_fp8.py` imports torch at module time")
    fake.add_review_comment(
        pr,
        101,
        "that is on purpose, `lazy_import` would break the `triton` path entirely.",
        author="owner",
        assoc="OWNER",
        in_reply_to=100,
    )
    _index(engine, fake, 1)

    (found,) = _mine(engine, mine.mine_rationale)

    assert "py" not in found.query.split()
    assert "lazy_import" in found.query


# -- failure ---------------------------------------------------------------


def test_an_error_two_threads_carry_is_a_candidate(engine: Engine, fake: FakeGitHub) -> None:
    for number in (1, 2):
        fake.add_issue(number, body="ValueError: backend should be defined in the mapping")
    _index(engine, fake, 1, 2)

    (found,) = _mine(engine, mine.mine_failure)

    assert found.kind == "failure"
    assert found.errors and "backend should be defined" in found.errors[0]
    assert sorted(r.number for r in found.relevant) == [1, 2]
    assert found.source["carriers"] == 2


def test_an_error_only_one_thread_carries_is_not_a_retrieval_question(
    engine: Engine, fake: FakeGitHub
) -> None:
    """Nothing to rank: with one carrier every system scores 1.0 or 0.0 and the example
    separates nothing."""
    fake.add_issue(1, body="ValueError: backend should be defined in the mapping")
    _index(engine, fake, 1)

    assert _mine(engine, mine.mine_failure) == []


def test_an_error_everything_carries_is_not_one_either(engine: Engine, fake: FakeGitHub) -> None:
    for number in range(1, 6):
        fake.add_issue(number, body="IndexError: list index out of range")
    _index(engine, fake, *range(1, 6))

    assert _mine(engine, mine.mine_failure, max_threads=4) == []


# -- precedent -------------------------------------------------------------


def test_a_named_precedent_is_a_candidate(engine: Engine, fake: FakeGitHub) -> None:
    fake.add_pr(41, title="Standardize the output collection")
    pr = fake.add_pr(42, title="Migrate the rest")
    fake.add_comment(pr, 500, "Following #41 we want `_can_record_outputs` on every model.")
    _index(engine, fake, 41, 42)

    (found,) = _mine(engine, mine.mine_precedent)

    assert found.kind == "precedent"
    assert [r.number for r in found.relevant] == [41]
    assert "_can_record_outputs" in found.query


def test_a_precedent_that_is_not_indexed_is_unanswerable(engine: Engine, fake: FakeGitHub) -> None:
    """Section 5.5's sample is a window. A reference out of it cannot be retrieved by
    anything, and scoring it would measure the window rather than the retrieval."""
    pr = fake.add_pr(42, title="Migrate the rest")
    fake.add_comment(pr, 500, "Following #41 we want `_can_record_outputs` on every model.")
    _index(engine, fake, 42)

    assert _mine(engine, mine.mine_precedent) == []


def test_fixes_is_a_link_not_a_precedent(engine: Engine, fake: FakeGitHub) -> None:
    """ "Fixes #41" names the thing being closed, not a prior change to imitate. That is
    milestone 4's `thread_links`, and mining it here would fill the slice with pairs that
    answer a different question."""
    fake.add_issue(41, title="the bug")
    pr = fake.add_pr(42, title="the fix")
    fake.add_comment(pr, 500, "Fixes #41 by rewriting `_can_record_outputs`.")
    _index(engine, fake, 41, 42)

    assert _mine(engine, mine.mine_precedent) == []


def test_a_thread_is_not_its_own_precedent(engine: Engine, fake: FakeGitHub) -> None:
    pr = fake.add_pr(42, title="Migrate the rest")
    fake.add_comment(pr, 500, "as in #42, `_can_record_outputs` goes on every model.")
    _index(engine, fake, 42)

    assert _mine(engine, mine.mine_precedent) == []


# -- the window ------------------------------------------------------------


def test_a_repo_with_no_sample_row_declares_no_floor(engine: Engine, fake: FakeGitHub) -> None:
    fake.add_issue(1)
    _index(engine, fake, 1)

    with engine.connect() as conn:
        (corpus,) = mine.corpus_of(conn, REPO)

    assert corpus.since is None
    assert corpus.threads == 1
