"""`grep` and `copies` over a tree (issue #7).

`copies` is the verb the report ranked first: a repository that duplicates model code on
purpose asks "which of the 38 copies diverge", and that is a grouping rather than a list.
"""

from __future__ import annotations

import pytest

from ghlore.code.survey import copies, grep, symbol_body

CANONICAL = """\
def compute_default_rope_parameters(config):
    base = config.rope_theta
    dim = int(config.head_dim * config.partial_rotary_factor)
    return base, dim
"""

DIVERGED = """\
def compute_default_rope_parameters(config):
    base = config.rope_theta
    dim = int(config.head_dim)
    return base, dim
"""


@pytest.fixture
def tree(tmp_path):
    models = tmp_path / "src" / "models"
    models.mkdir(parents=True)
    for name in ("llama", "gemma", "mistral"):
        (models / f"modeling_{name}.py").write_text(CANONICAL)
    (models / "modeling_gpt_neox_japanese.py").write_text(DIVERGED)
    (tmp_path / "README.md").write_text("partial_rotary_factor is documented here")
    return tmp_path


def test_copies_groups_by_body_so_the_outlier_is_visible(tree) -> None:
    """`huggingface/transformers#48630` in miniature: one model out of four diverging, and
    the useful answer is which one -- not where the 38 definitions are."""
    result = copies(str(tree), "compute_default_rope_parameters")

    assert len(result.copies) == 4
    groups = list(result.groups.values())
    assert [len(group) for group in groups] == [3, 1]
    assert groups[1][0].path.endswith("modeling_gpt_neox_japanese.py")


def test_copies_ignores_indentation_so_a_lifted_function_is_not_a_divergence(tmp_path) -> None:
    (tmp_path / "a.py").write_text(CANONICAL)
    (tmp_path / "b.py").write_text(
        "class Model:\n" + "".join(f"    {line}\n" for line in CANONICAL.splitlines())
    )

    result = copies(str(tmp_path), "compute_default_rope_parameters")

    assert len(result.copies) == 2
    assert len(result.groups) == 1


def test_grep_answers_the_pattern_question_a_symbol_lookup_cannot(tree) -> None:
    """ "which files have this shape of code" is not "where is this symbol defined"."""
    result = grep(str(tree), "partial_rotary_factor", path_glob="src/**/modeling_*.py")

    assert [hit.path for hit in result.hits] == [
        "src/models/modeling_gemma.py",
        "src/models/modeling_llama.py",
        "src/models/modeling_mistral.py",
    ]
    assert all(hit.line == 3 for hit in result.hits)


def test_grep_path_glob_excludes_what_it_should(tree) -> None:
    everywhere = grep(str(tree), "partial_rotary_factor")
    assert any(hit.path == "README.md" for hit in everywhere.hits)


def test_symbol_returns_a_body_and_how_many_there_are(tree) -> None:
    """One of 38 served as *the* body is a wrong answer the caller cannot see, so the
    count travels with it."""
    found = symbol_body(str(tree), "compute_default_rope_parameters")

    assert found is not None
    assert found.definition.qualname == "compute_default_rope_parameters"
    assert found.total == 4
    assert found.path.startswith("src/models/")
    assert "def compute_default_rope_parameters" in found.body


def test_a_symbol_that_is_not_there_is_none_rather_than_an_error(tree) -> None:
    assert symbol_body(str(tree), "not_a_function") is None
