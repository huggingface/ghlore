"""Folding the UI's per-hit verdicts into per-question ground truth (section 10)."""

from __future__ import annotations

import pytest

from relore.bench.dataset import Dataset, Example
from relore.bench.judge import fold


def _label(**kwargs) -> dict:
    row = {
        "query": "load_adapter",
        "kind": "rationale",
        "repo": "owner/name",
        "number": 1,
        "source_type": "review_comment",
        "verdict": "relevant",
        "filters": {},
        "backend": {"ranking": "ts_rank_cd"},
    }
    row.update(kwargs)
    return row


def _candidate(**kwargs) -> Example:
    kwargs.setdefault("id", "c1")
    kwargs.setdefault("kind", "rationale")
    kwargs.setdefault("query", "load_adapter")
    return Example(**kwargs)


def test_a_label_judges_the_candidate_it_matches() -> None:
    data = Dataset(examples=(_candidate(),))

    folded, stats = fold(data, [_label(), _label(number=2, verdict="not_relevant")])

    (example,) = folded.examples
    assert example.judged_by == "human"
    assert [r.number for r in example.relevant] == [1]
    assert stats.judged == 1


def test_a_question_with_no_relevant_thread_is_dropped_not_scored_zero() -> None:
    """The corpus not holding the answer is not a retrieval failure, and averaging it in
    moves every system's recall for a reason that is not about retrieval."""
    data = Dataset(examples=(_candidate(),))

    folded, stats = fold(data, [_label(verdict="not_relevant")])

    assert folded.examples[0].judged_by is None
    assert stats.dropped == ["[rationale] load_adapter"]


def test_decisive_counts_as_found() -> None:
    folded, _ = fold(Dataset(examples=(_candidate(),)), [_label(verdict="decisive")])

    assert folded.examples[0].judged_by == "human"


def test_changing_your_mind_keeps_the_last_click() -> None:
    folded, _ = fold(
        Dataset(examples=(_candidate(),)),
        [_label(verdict="relevant"), _label(verdict="not_relevant")],
    )

    assert folded.examples[0].judged_by is None


def test_the_same_words_under_a_different_file_is_a_different_question() -> None:
    data = Dataset(
        examples=(
            _candidate(id="a", files=("src/one.py",)),
            _candidate(id="b", files=("src/two.py",)),
        )
    )

    folded, stats = fold(data, [_label(filters={"files": ["src/two.py"]})])

    assert folded.examples[0].judged_by is None
    assert folded.examples[1].judged_by == "human"
    assert stats.ambiguous == []


def test_two_candidates_a_label_cannot_tell_apart_are_reported_not_guessed() -> None:
    data = Dataset(examples=(_candidate(id="a"), _candidate(id="b")))

    folded, stats = fold(data, [_label()])

    assert [ex.judged_by for ex in folded.examples] == [None, None]
    assert stats.ambiguous == ["[rationale] load_adapter"]


def test_a_query_typed_in_the_ui_becomes_a_new_example() -> None:
    """Section 10 wants the set to be a byproduct of using the search, not a second
    chore -- so a question mining never thought of still lands in it."""
    folded, stats = fold(Dataset(), [_label(query="rope_layer_types", number=9)])

    assert stats.minted == 1
    (example,) = folded.examples
    assert example.query == "rope_layer_types"
    assert example.judged_by == "human"
    assert [r.number for r in example.relevant] == [9]


def test_labels_from_two_backends_are_visible() -> None:
    _, stats = fold(
        Dataset(examples=(_candidate(),)),
        [_label(), _label(number=2, backend={"ranking": "bm25"})],
    )

    assert stats.backends == {"ts_rank_cd", "bm25"}


def test_a_frozen_set_cannot_be_judged() -> None:
    """Judging is what makes ground truth, so doing it after the freeze changes what a
    published number was measured against."""
    data = Dataset(examples=(_candidate(),)).freeze()

    with pytest.raises(ValueError, match="frozen"):
        fold(data, [_label(query="something else", number=4)])
