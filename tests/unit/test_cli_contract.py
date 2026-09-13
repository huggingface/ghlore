"""Contract bits that hold from commit one, so they cannot quietly regress later."""

from __future__ import annotations

import sys

import pytest

from ghlore import cli, daemon
from ghlore.code import registry


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
    """`ghlore search ... --json` is what anyone writes by analogy with `git` and `gh`, and
    argparse called the flag *unrecognized* rather than misplaced -- which sends the reader
    looking for a typo (huggingface/ghlore#6)."""
    assert cli.build_parser().parse_args(argv).json is True


def test_the_subparser_alias_does_not_overwrite_a_flag_given_before_the_verb() -> None:
    """The trap in accepting it twice: a subparser default would silently turn
    `ghlore --json search` back off."""
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
