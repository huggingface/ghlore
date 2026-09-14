"""Chunk boundaries (section 5.4) and content-hash stability (section 5.1).

The whole incremental story rests on the hash: if it is not stable across processes, every
poll rewrites every document and ``unchanged`` never happens.
"""

from __future__ import annotations

import subprocess
import sys

from relore.ingest.chunk import MAX_CHUNK_CHARS, chunk, content_hash


def test_a_short_body_is_one_document() -> None:
    assert [c.text for c in chunk("a short comment")] == ["a short comment"]


def test_empty_produces_nothing() -> None:
    assert chunk("   \n  ") == []


def test_split_false_never_splits_however_long() -> None:
    """A title, and an inline review comment tied to one file and line."""
    body = "x" * (MAX_CHUNK_CHARS * 3)
    pieces = chunk(body, split=False)
    assert len(pieces) == 1
    assert pieces[0].text == body


def test_a_long_body_splits_on_headings() -> None:
    filler = "word " * 400  # ~2000 chars per section
    body = "\n\n".join(f"## Section {i}\n\n{filler}" for i in range(5))
    pieces = chunk(body)
    assert len(pieces) > 1
    assert all(p.text.startswith("## Section") for p in pieces)
    assert [p.index for p in pieces] == list(range(len(pieces)))


def test_a_single_oversized_paragraph_is_cut_on_length() -> None:
    """A pasted log or tensor dump has no natural boundary inside it."""
    pieces = chunk("y" * (MAX_CHUNK_CHARS * 2 + 10))
    assert len(pieces) == 3
    assert all(len(p.text) <= MAX_CHUNK_CHARS for p in pieces)
    assert "".join(p.text for p in pieces) == "y" * (MAX_CHUNK_CHARS * 2 + 10)


def test_no_chunk_exceeds_the_limit_when_headings_are_huge() -> None:
    body = "# One\n\n" + ("z" * (MAX_CHUNK_CHARS + 50))
    assert all(len(p.text) <= MAX_CHUNK_CHARS for p in chunk(body))


def test_the_chunking_version_is_inside_the_hash() -> None:
    """Bumping it must invalidate every document exactly once, on purpose."""
    assert content_hash("same text", chunking_version=1) != content_hash(
        "same text", chunking_version=2
    )


def test_the_hash_is_stable_across_processes() -> None:
    here = content_hash("a body with an identifier: forward()")
    code = (
        "from relore.ingest.chunk import content_hash;"
        "print(content_hash('a body with an identifier: forward()'))"
    )
    # A different hash seed is the classic way this breaks -- `hash()` is randomized per
    # process, `sha256` is not, and the test exists to keep it that way.
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONHASHSEED": "12345", "PATH": "/usr/bin:/bin"},
    )
    assert out.stdout.strip() == here
