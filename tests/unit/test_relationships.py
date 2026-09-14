"""Which pull request claims to close which thread (section 13.3).

The edge exists to answer one question -- *is somebody already fixing this?* -- so what is
asserted here is mostly what is **not** an edge. A manufactured link is worse than a
missing one: it tells an agent to stand down over a pull request that never claimed
anything.
"""

from __future__ import annotations

import pytest

from relore.ingest.relationships import extract_links

REPO = "owner/name"


def _body(text: str, *, chunk: int = 0) -> dict[str, object]:
    return {"source_type": "body", "chunk_index": chunk, "body_markdown": text}


def _links(*documents, thread_type: str = "pr", number: int = 100, detail=None):
    return extract_links(thread_type, number, list(documents), detail, repo=REPO)


@pytest.mark.parametrize(
    "text",
    [
        "Fixes #12",
        "fixes #12",
        "FIXED #12",
        "Fixes: #12",
        "closes #12",
        "Closed #12",
        "resolve #12",
        "Resolves  #12",
        "some prose\n\nFixes #12\n\nmore prose",
    ],
)
def test_githubs_own_closing_keywords_are_edges(text: str) -> None:
    assert _links(_body(text)) == [
        {"relationship": "closes", "target_number": 12, "target_thread_id": None}
    ]


@pytest.mark.parametrize(
    "text",
    [
        "see #12 for context",
        "related to #12",
        "this supersedes the approach in #12",
        "unfixable, see #12",
    ],
)
def test_a_bare_cross_reference_is_not_a_claim(text: str) -> None:
    """Most `#N` in a pull request body is context. Reading every one as "closes" would
    tell an agent that a pull request is already fixing something it only mentioned."""
    assert _links(_body(text)) == []


def test_an_issue_body_is_prose_not_a_claim() -> None:
    """GitHub applies the keyword in a pull request. Extracting it from an issue would
    draw an edge GitHub itself does not."""
    assert _links(_body("Fixes #12"), thread_type="issue") == []


def test_a_comment_is_not_the_pull_requests_own_claim() -> None:
    comment = {"source_type": "issue_comment", "body_markdown": "this fixes #12 I think"}
    assert _links(comment) == []


def test_every_chunk_of_a_long_body_is_read() -> None:
    """A closing line lands in the second chunk of a long body often enough to matter."""
    assert _links(_body("intro"), _body("Fixes #12", chunk=1))[0]["target_number"] == 12


def test_a_thread_cannot_close_itself() -> None:
    assert _links(_body("Fixes #100 and fixes #12"), number=100) == [
        {"relationship": "closes", "target_number": 12, "target_thread_id": None}
    ]


def test_githubs_resolved_references_are_read_too() -> None:
    """The definitive source, and it arrives only with the per-PR pass over *merged* pull
    requests -- which is why the body is read as well: an in-flight pull request has no
    GraphQL node yet."""
    detail = {
        "closingIssuesReferences": {"nodes": [{"number": 7, "repository": {"nameWithOwner": REPO}}]}
    }
    assert [link["target_number"] for link in _links(_body("no keyword here"), detail=detail)] == [
        7
    ]


def test_a_closing_reference_in_another_repository_is_dropped() -> None:
    """`closingIssuesReferences` returns a bare number, and a number from a foreign
    repository would resolve against ours and claim the wrong thread."""
    detail = {
        "closingIssuesReferences": {
            "nodes": [{"number": 7, "repository": {"nameWithOwner": "someone/else"}}]
        }
    }
    assert _links(_body("nothing"), detail=detail) == []


def test_the_two_sources_are_merged_without_duplicates() -> None:
    detail = {
        "closingIssuesReferences": {
            "nodes": [{"number": 12, "repository": {"nameWithOwner": REPO}}]
        }
    }
    assert len(_links(_body("Fixes #12"), detail=detail)) == 1
