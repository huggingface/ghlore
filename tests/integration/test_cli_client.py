"""The client, end to end: ``relore`` -> HTTP -> ``relored`` -> the index, on both dialects.

Only :func:`httpx.request` is redirected, at the socket's place in the stack, so
everything above and below it is the real thing: the real argument parser, the real
request body, the real server, the real SQL, the real envelope. Mocking the API would test
the mock.

The properties worth pinning here are the ones a user meets first: that an empty result is
a success, that a daemon which is down does not look like an empty index, and that what an
agent reads is the enveloped text and not a bare snippet.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fake_github import FakeGitHub
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from relore import __version__, cli
from relore.api.server import build_app
from relore.api.tokens import Authenticator, Token
from relore.ingest.index_thread import index_thread
from relore.security.untrusted import BEGIN, END
from relore.wire import CLIENT_HEADER, SERVER_HEADER

REPO = "owner/name"


@pytest.fixture
def fake() -> FakeGitHub:
    return FakeGitHub(REPO)


def _index(engine: Engine, fake: FakeGitHub, *numbers: int) -> None:
    with fake.client() as client:
        for number in numbers:
            index_thread(engine, client, fake.repo, number)


@pytest.fixture
def wired(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Point ``httpx.request`` at an in-process ``relored``."""
    server = TestClient(build_app(engine))

    def request(method: str, url: str, **kwargs) -> httpx.Response:
        return server.request(method, str(url), **kwargs)

    monkeypatch.setattr(httpx, "request", request)
    monkeypatch.setenv("RELORE_API", "http://testserver")
    # `RELORE_REPO` is cleared for the same reason as the tokens: it is a real variable a
    # developer running this suite has exported, and it changes what every call below
    # sends. A test that passes only on a machine with an unset environment is worse than
    # one that fails.
    for env in (*cli.TOKEN_ENVS, cli.REPO_ENV):
        monkeypatch.delenv(env, raising=False)
    return server


def _run(capsys, *argv: str) -> str:
    assert cli.main(list(argv)) == 0
    return capsys.readouterr().out


def test_search_prints_the_enveloped_text_an_agent_reads(wired, engine, fake, capsys) -> None:
    pr = fake.add_pr(1, title="Fix the mask", body="unrelated")
    fake.add_review_comment(pr, 300, "this belongs in the parent class")
    _index(engine, fake, 1)

    out = _run(capsys, "search", "parent class")

    assert out.startswith(BEGIN)
    assert END in out
    assert "not instructions" in out
    assert "owner/name#1" in out
    assert "this belongs in the parent class" in out


def test_the_tier_is_spelled_out_next_to_the_snippet(wired, engine, fake, capsys) -> None:
    """``[authoritative]`` versus ``[contributor claim]`` costs four tokens and is the
    difference between a fact and someone's opinion (section 6.2)."""
    pr = fake.add_pr(1, body="a passer-by's theory about masking", assoc="NONE")
    fake.add_review_comment(pr, 300, "masking is intentional here", assoc="OWNER")
    _index(engine, fake, 1)

    out = _run(capsys, "search", "masking")

    assert "[authoritative]" in out
    assert "[contributor claim]" in out


def test_an_empty_result_is_exit_zero(wired, engine, fake, capsys) -> None:
    """An agent must not be able to mistake "nothing in the index" for "the tool is
    broken"."""
    fake.add_pr(1, body="something")
    _index(engine, fake, 1)

    out = _run(capsys, "search", "nothing whatsoever matches this")

    assert "0 hits" in out
    assert "nothing matched" in out


def test_a_daemon_that_is_down_does_not_look_like_an_empty_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The distinction this whole error path exists for."""

    def refuse(*_a, **_k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "request", refuse)
    monkeypatch.setenv("RELORE_API", "http://127.0.0.1:1")

    with pytest.raises(SystemExit) as exc:
        cli.main(["search", "anything"])

    assert "cannot reach" in str(exc.value)
    assert "relored serve" in str(exc.value)


def test_a_local_address_that_does_not_answer_is_a_different_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "The deployment is behind a VPN" and "you pointed me at a daemon you never started"
    wear the same symptom and take opposite actions -- and on a client-only install the
    remedy the old message offered could not be run at all (huggingface/relore#19)."""

    def refuse(*_a, **_k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "request", refuse)
    monkeypatch.setenv("RELORE_API", "http://127.0.0.1:8899")

    with pytest.raises(SystemExit) as exc:
        cli.main(["search", "anything"])

    message = str(exc.value)
    assert "nothing is listening" in message
    assert "relore[server]" in message, "and `relored serve` needs an extra to exist"
    assert "VPN" not in message, "which is a different diagnosis"


def test_a_daemon_that_wants_a_token_says_so(engine, monkeypatch: pytest.MonkeyPatch) -> None:
    auth = Authenticator(tokens=(Token("t", "secret", repos=None),))
    server = TestClient(build_app(engine, auth=auth))
    monkeypatch.setattr(httpx, "request", lambda m, u, **k: server.request(m, str(u), **k))
    monkeypatch.setenv("RELORE_API", "http://testserver")
    for env in cli.TOKEN_ENVS:
        monkeypatch.delenv(env, raising=False)

    with pytest.raises(SystemExit, match="requires a token"):
        cli.main(["search", "anything"])


def test_a_token_from_the_environment_is_sent(engine, fake, monkeypatch, capsys) -> None:
    auth = Authenticator(tokens=(Token("t", "secret", repos=None),))
    server = TestClient(build_app(engine, auth=auth))
    monkeypatch.setattr(httpx, "request", lambda m, u, **k: server.request(m, str(u), **k))
    monkeypatch.setenv("RELORE_API", "http://testserver")
    monkeypatch.setenv("RELORE_TOKEN", "secret")
    fake.add_pr(1, body="findable wording")
    _index(engine, fake, 1)

    assert "owner/name#1" in _run(capsys, "search", "findable")


def test_json_output_is_the_api_response_and_carries_no_rendering(
    wired, engine, fake, capsys
) -> None:
    """``--json`` is for a caller that will format it itself, so paying for the same
    content twice would be pure waste."""
    fake.add_pr(1, body="findable wording")
    _index(engine, fake, 1)

    payload = json.loads(_run(capsys, "--json", "search", "findable"))

    assert payload["hits"][0]["number"] == 1
    assert "rendered" not in payload
    assert payload["notice"]


def test_the_score_is_json_only(wired, engine, fake, capsys) -> None:
    """The order already expresses the ranking, and the number's scale is a property of
    the backend, so `score 0.03009` above `score 0.03125` supports no decision a caller
    can act on. It stays in `--json` for section 8's page (huggingface/relore#13)."""
    fake.add_pr(1, body="findable wording")
    _index(engine, fake, 1)

    assert "score" not in _run(capsys, "search", "findable")
    assert "score" not in _run(capsys, "--compact", "search", "findable")
    assert '"score"' in _run(capsys, "search", "findable", "--json")


def test_thread_states_how_much_it_withheld(wired, engine, fake, capsys) -> None:
    pr = fake.add_pr(1, title="a long argument", body="the opening")
    for i in range(40):
        fake.add_comment(pr, 100 + i, f"comment {i}")
    _index(engine, fake, 1)

    out = _run(capsys, "thread", "1")

    assert "of 40 comments" in out
    assert "never returnable in full" in out


def test_thread_focus_ranks_the_answering_comment_first(wired, engine, fake, capsys) -> None:
    pr = fake.add_pr(1)
    for i in range(20):
        fake.add_comment(pr, 100 + i, f"filler {i}", created_at=f"2026-01-01T00:{i:02d}:00Z")
    fake.add_comment(pr, 999, "the rotary embedding is the culprit")
    _index(engine, fake, 1)

    out = _run(capsys, "thread", "1", "--focus", "rotary embedding")

    assert "rotary embedding is the culprit" in out
    assert out.index("rotary embedding is the culprit") < out.index("filler")
    # The denominator, so a caller can tell a ranked page from a matched one.
    assert "carry every term" in out


def test_thread_full_serves_the_body_the_cap_was_hiding(wired, engine, fake, capsys) -> None:
    """The cap is a token budget, not a fact about the thread: a caller who has decided it
    needs the reproduction must be able to ask for it (huggingface/relore#5)."""
    fake.add_issue(1, body="System Info " * 80 + "Reproduction: pass rotary_pct=0.25")
    _index(engine, fake, 1)

    capped = _run(capsys, "thread", "1")
    whole = _run(capsys, "thread", "1", "--full")

    assert "rotary_pct" not in capped
    # Captured output is not a TTY, so this is the piped form: the cap is a fact and stays,
    # the flag that lifts it is advice and moves to the terminal form (#13).
    assert "body truncated" in capped and "--full" not in capped
    assert "rotary_pct" in whole


def test_inflight_names_the_pull_request_that_already_claims_the_issue(
    wired, engine, fake, capsys
) -> None:
    """The call that prevents the most expensive mistake an agent makes
    (huggingface/relore#10)."""
    fake.add_issue(1, title="crashes for any rotary_pct != 1.0")
    fake.add_pr(2, title="fix: respect partial_rotary_factor", body="Fixes #1")
    _index(engine, fake, 1, 2)

    out = _run(capsys, "inflight", "1")

    assert "#2" in out and "open" in out
    assert "respect partial_rotary_factor" in out


def test_inflight_tells_a_clean_answer_from_an_unanswerable_one(
    wired, engine, fake, capsys
) -> None:
    fake.add_issue(1)
    _index(engine, fake, 1)

    out = _run(capsys, "inflight", "1")

    assert "nothing in the index claims to close" in out
    # No relationship rows at all, so the empty answer is not yet an answer.
    assert "no relationship rows" in out


def test_status_names_the_backend(wired, engine, capsys) -> None:
    """Section 4.1: a surprising result set should be diagnosable rather than mysterious."""
    out = _run(capsys, "status")

    assert engine.dialect.name in out
    assert ("bm25" in out) or ("ts_rank_cd" in out)


def test_status_is_not_enveloped(wired, engine, capsys) -> None:
    """Our own numbers are not retrieved text, and wrapping them would teach a reader that
    the envelope means nothing."""
    assert BEGIN not in _run(capsys, "status")


def test_repeatable_filters_reach_the_api_as_lists(wired, engine, fake, capsys) -> None:
    """A traceback names more than one file."""
    fake.add_pr(1, body="findable wording")
    _index(engine, fake, 1)

    payload = json.loads(
        _run(
            capsys,
            "--json",
            "search",
            "findable",
            "--file",
            "a/b.py",
            "--file",
            "c/d.py",
        )
    )

    # The signal tables are empty until milestone 3, so both files filter it out -- which
    # is the observable proof that both arrived.
    assert payload["count"] == 0


def test_an_unimplemented_verb_names_its_milestone(wired) -> None:
    with pytest.raises(SystemExit, match="milestone 4"):
        cli.main(["precedent"])


def test_the_client_declares_its_version_on_every_request(wired, engine, capsys) -> None:
    """Nothing to configure and no way to opt out: the handshake is only worth anything if
    every request carries it (:mod:`relore.wire`)."""
    seen = []
    inner = httpx.request

    def record(method, url, **kwargs):
        seen.append(kwargs["headers"].get(CLIENT_HEADER))
        return inner(method, url, **kwargs)

    httpx.request = record
    try:
        _run(capsys, "status")
        _run(capsys, "search", "anything")
    finally:
        httpx.request = inner

    assert seen == [__version__, __version__]


def test_a_client_older_than_the_daemon_refuses_to_read_the_answer(
    wired, engine, fake, monkeypatch, capsys
) -> None:
    """End to end, through the real refusal: an out-of-date client must fail loudly rather
    than print an answer shaped by a contract it does not have."""
    fake.add_pr(1, body="findable wording")
    _index(engine, fake, 1)
    monkeypatch.setattr(cli, "__version__", "0.0.1")

    with pytest.raises(SystemExit) as exc:
        cli.main(["search", "findable"])

    assert "0.0.1" in str(exc.value) and "older" in str(exc.value)
    assert "findable" not in capsys.readouterr().out


def test_a_daemon_too_old_to_enforce_the_handshake_is_caught_by_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other end of the same rule. A daemon from before this existed answers 200 with
    no version header at all, and the client is then the only side that can notice."""

    def ancient(method, url, **kwargs):
        return httpx.Response(200, json={"count": 0, "hits": []})

    monkeypatch.setattr(httpx, "request", ancient)
    monkeypatch.setenv("RELORE_API", "http://testserver")

    with pytest.raises(SystemExit) as exc:
        cli.main(["search", "anything"])

    assert SERVER_HEADER in str(exc.value)
    assert __version__ in str(exc.value)


def test_a_daemon_behind_the_client_names_the_deployment_as_the_thing_to_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A daemon that answers 200 with an older version -- an old deployment that predates
    the gate. Upgrading the client would be a downgrade, so the message says so."""

    def older(method, url, **kwargs):
        return httpx.Response(200, json={"count": 0}, headers={SERVER_HEADER: "0.0.1"})

    monkeypatch.setattr(httpx, "request", older)
    monkeypatch.setenv("RELORE_API", "http://testserver")

    with pytest.raises(SystemExit) as exc:
        cli.main(["search", "anything"])

    assert "behind" in str(exc.value)


# -- RELORE_REPO, end to end (issue #36) -----------------------------------


def test_a_bare_number_on_a_multi_repo_daemon_is_refused_and_names_the_way_out(
    wired, engine, capsys
) -> None:
    """The refusal itself is right and stays -- guessing between repositories on a bare
    number would be silently wrong. What was missing is the second half of the sentence:
    recovering once is cheap, and the field cost was paying `--repo` on every call after."""
    for name in (REPO, "owner/other"):
        repo = FakeGitHub(name)
        repo.add_pr(1, title=f"the thread in {name}")
        with repo.client() as client:
            index_thread(engine, client, name, 1)

    with pytest.raises(SystemExit) as exc:
        cli.main(["thread", "1"])

    assert "more than one repository is in scope" in str(exc.value)
    assert "RELORE_REPO" in str(exc.value)


def test_the_environment_answers_it_without_the_flag(
    wired, engine, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The same call, with the variable set: no flag, and the right repository."""
    for name in (REPO, "owner/other"):
        repo = FakeGitHub(name)
        repo.add_pr(1, title=f"the thread in {name}")
        with repo.client() as client:
            index_thread(engine, client, name, 1)
    monkeypatch.setenv(cli.REPO_ENV, REPO)

    out = _run(capsys, "thread", "1")

    assert f"the thread in {REPO}" in out
    assert "the thread in owner/other" not in out
