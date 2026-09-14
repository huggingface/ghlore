"""Contract bits that hold from commit one, so they cannot quietly regress later."""

from __future__ import annotations

import sys

import pytest

from relore import cli, daemon
from relore.code import registry


@pytest.mark.parametrize("mod", [cli, daemon])
def test_version_flag_exits_clean(mod, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        mod.main(["--version"])
    assert exc.value.code == 0


@pytest.mark.parametrize("verb", ["map", "defs", "refs"])
def test_code_verbs_without_a_parser_give_an_install_hint(monkeypatch, tmp_path, verb) -> None:
    """Never an ImportError traceback -- AGENTS.md, and the build plan section 2.

    Both halves are removed, because either alone can answer: no grammar (so the shipped
    tree-sitter provider fails to construct, and is skipped with a warning) *and* no ctags
    fallback. That is the only state in which there is genuinely nothing to say but "install
    something", and asserting it with a grammar still reachable would assert nothing.

    ``map`` is in here for a specific near-miss: it walks a tree, nothing claims any file,
    so before ``registry.require_any`` it returned an empty map and exit 0 -- a missing
    install wearing the answer to a quiet repository.
    """
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    monkeypatch.setenv(registry.DISABLE_CTAGS_ENV, "1")
    registry.reset()
    # A file that exists, so `defs` fails for the reason under test rather than on the read.
    source = tmp_path / "m.py"
    source.write_text("def f():\n    pass\n")
    argv = {"map": [str(tmp_path)], "defs": [str(source)], "refs": ["f"]}[verb]

    with pytest.raises(SystemExit) as exc:
        cli.main([verb, *argv])

    assert "pip install" in str(exc.value)
    registry.reset()


@pytest.mark.parametrize(
    "verb",
    ["search", "thread", "inflight", "precedent", "why", "status", "map", "defs", "refs"],
)
def test_every_documented_verb_is_registered(verb: str) -> None:
    actions = [a for a in cli.build_parser()._actions if hasattr(a, "choices") and a.choices]
    assert any(verb in a.choices for a in actions), f"{verb} is documented but not in the parser"


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "refs", "helper"],
        ["refs", "helper", "--json"],
        ["refs", "--json", "helper"],
    ],
)
def test_a_global_flag_is_accepted_on_either_side_of_the_verb(argv: list[str]) -> None:
    """`relore search ... --json` is what anyone writes by analogy with `git` and `gh`, and
    argparse called the flag *unrecognized* rather than misplaced -- which sends the reader
    looking for a typo (huggingface/relore#6)."""
    assert cli.build_parser().parse_args(argv).json is True


def test_the_subparser_alias_does_not_overwrite_a_flag_given_before_the_verb() -> None:
    """The trap in accepting it twice: a subparser default would silently turn
    `relore --json search` back off."""
    args = cli.build_parser().parse_args(["--compact", "search", "anything"])

    assert args.compact is True
    assert args.json is False


@pytest.mark.parametrize("verb", ["precedent"])
def test_an_unimplemented_verb_says_so_in_its_help(verb: str) -> None:
    """An agent builds its plan from `--help`, so a stub has to say it is one. `why` was
    on this list until #9 built it."""
    parser = cli.build_parser()
    subparsers = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
    entry = next(c for c in subparsers._choices_actions if c.dest == verb)

    assert "NOT IMPLEMENTED" in (entry.help or "")


def test_why_is_implemented_and_takes_a_path_and_a_line() -> None:
    args = cli.build_parser().parse_args(["why", "src/model.py:90"])

    assert args.location == "src/model.py:90"
    assert "NOT IMPLEMENTED" not in (
        next(
            c
            for c in next(
                a for a in cli.build_parser()._actions if hasattr(a, "choices") and a.choices
            )._choices_actions
            if c.dest == "why"
        ).help
        or ""
    )


def test_precedent_takes_a_limit_like_every_other_listing_verb() -> None:
    assert cli.build_parser().parse_args(["precedent", "--limit", "3"]).limit == 3


def test_the_default_api_is_the_deployment_and_is_overridable() -> None:
    """The default moved to `ghlore.huggingface.tech` on 2026-09-11, when the name began
    to resolve. It is pinned here because it was reverted once for a good reason — the name
    existed and did not resolve — so a future change to it should be deliberate rather than
    incidental. The override is the part that has to keep working: a caller with their own
    index must never be silently pointed at production.
    """
    assert cli.DEFAULT_API == "https://ghlore.huggingface.tech"
    assert cli.build_parser().parse_args(["--api", "http://127.0.0.1:9999", "status"]).api == (
        "http://127.0.0.1:9999"
    )


# -- RELORE_REPO (issue #36) -----------------------------------------------


@pytest.mark.parametrize("verb,argv", [("thread", ["42"]), ("why", ["a.py:1"]), ("grep", ["x"])])
def test_the_environment_supplies_the_repository_when_the_flag_does_not(
    monkeypatch, verb: str, argv: list[str]
) -> None:
    """The per-call tax this removes: a daemon serving three repositories makes `--repo`
    mandatory on every bare number, and one field run passed it on ~20 consecutive calls."""
    monkeypatch.setenv(cli.REPO_ENV, "huggingface/transformers")
    args = cli.build_parser().parse_args([verb, *argv])

    assert cli._repo(args) == "huggingface/transformers"


def test_the_flag_beats_the_environment(monkeypatch) -> None:
    """So a session with a default set can still ask about another repository without
    unsetting anything."""
    monkeypatch.setenv(cli.REPO_ENV, "huggingface/transformers")
    args = cli.build_parser().parse_args(["thread", "42", "--repo", "huggingface/trl"])

    assert cli._repo(args) == "huggingface/trl"


def test_an_empty_variable_reads_as_unset_rather_than_as_a_repository(monkeypatch) -> None:
    """`export RELORE_REPO=` must hand the question back to the daemon, not ask it about a
    repository named "", which is a 404 with a confusing sentence in it."""
    monkeypatch.setenv(cli.REPO_ENV, "")

    assert cli._repo(cli.build_parser().parse_args(["thread", "42"])) is None
    assert cli._repos(cli.build_parser().parse_args(["search", "x"])) == []


def test_search_takes_the_default_too_but_the_flag_replaces_it(monkeypatch) -> None:
    """`search` spans the whole scope by design, so this is the one verb the variable
    *narrows*. That is what naming the session's repository asks for."""
    monkeypatch.setenv(cli.REPO_ENV, "huggingface/transformers")

    assert cli._repos(cli.build_parser().parse_args(["search", "x"])) == [
        "huggingface/transformers"
    ]
    assert cli._repos(
        cli.build_parser().parse_args(["search", "x", "--repo", "huggingface/trl"])
    ) == ["huggingface/trl"]


def test_the_environment_never_moves_defs_and_refs_off_the_working_tree(monkeypatch) -> None:
    """For these two `--repo` does not name a repository, it *switches the verb* to the
    daemon's clone of HEAD. The tree you are standing in, uncommitted edits included, is the
    only thing they offer that nothing else does, so only the flag may give it up."""
    monkeypatch.setenv(cli.REPO_ENV, "huggingface/transformers")

    assert cli.build_parser().parse_args(["defs", "a.py"]).repo is None
    assert cli.build_parser().parse_args(["refs", "helper"]).repo is None


# -- what the code verbs print ---------------------------------------------


def test_grep_names_both_denominators() -> None:
    """Issue #37: the summary read `3 in 4 files`, where 4 counted the files *searched*.
    The natural reading is the other one, and an agent sizing blast radius acts on it."""
    text = cli._grep_text(
        {
            "hits": [
                {"path": "a.py", "line": 1, "text": "x"},
                {"path": "a.py", "line": 2, "text": "x"},
                {"path": "b.py", "line": 9, "text": "x"},
            ],
            "files_searched": 4,
        }
    )

    assert text.splitlines()[-1] == "-- 3 hits in 2 of 4 files searched"


def test_grep_says_one_hit_rather_than_1_hits() -> None:
    text = cli._grep_text({"hits": [{"path": "a.py", "line": 1, "text": "x"}], "files_searched": 1})

    assert text.splitlines()[-1] == "-- 1 hit in 1 of 1 file searched"


def test_grep_still_discloses_its_cap_with_the_remedy_in_it() -> None:
    """The one thing this command already got right, and worth keeping while rewording
    around it: the cap says so, inline, with what to do about it."""
    text = cli._grep_text({"hits": [], "files_searched": 3, "truncated": True})

    assert "(capped: narrow it with --path)" in text


def test_copies_calls_out_the_shape_of_one() -> None:
    """On a symbol with 186 definitions the singleton is the row the question was about,
    and it sorts last, under everything else (issue #35)."""
    text = cli._copies_text(
        {
            "symbol": "compute_default_rope_parameters",
            "total": 3,
            "groups": [
                {"count": 2, "copies": [{"path": "a.py", "start_line": 1}] * 2},
                {"count": 1, "copies": [{"path": "odd.py", "start_line": 90}]},
            ],
        }
    )

    assert "-- shape 1: 2 copies (the majority shape)" in text
    assert "-- shape 2: 1 copy  <- the only one of its shape" in text


def test_copies_says_what_it_normalized_away() -> None:
    """The answer changed shape -- a group is now "these do the same thing" rather than
    "these are the same text" -- and a reader not told that reads a divergence as a typo."""
    grouped = cli._copies_text({"symbol": "f", "total": 1, "groups": [{"count": 1, "copies": []}]})
    exact = cli._copies_text(
        {"symbol": "f", "total": 1, "exact": True, "groups": [{"count": 1, "copies": []}]}
    )

    assert "normalized away" in grouped and "--exact" in grouped
    assert "exact text" in exact and "normalized away" not in exact
