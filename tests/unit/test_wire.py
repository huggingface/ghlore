"""The version handshake's one decision: what to say about a difference.

The transport is tested in ``tests/integration`` -- here is the sentence itself, because
its whole job is telling the reader *which end to move*, and a message that gets that
backwards costs an hour on the wrong repository.
"""

from __future__ import annotations

import pathlib
import re

from relore import __version__
from relore.wire import CLIENT_HEADER, explain

PYPROJECT = pathlib.Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_the_same_version_is_not_a_problem() -> None:
    assert explain(__version__) is None
    assert explain("1.2.3", "1.2.3") is None


def test_an_older_client_is_told_to_upgrade_itself() -> None:
    message = explain("0.2.9", "0.3.0")

    assert message is not None
    assert "older" in message and "pip install" in message


def test_a_newer_client_is_told_the_deployment_is_behind() -> None:
    """The inverse instruction. "Upgrade the client" here would be a downgrade, and the
    thing that is actually out of date is the deployment."""
    message = explain("0.4.0", "0.3.0")

    assert message is not None
    assert "behind" in message
    assert "pip install" not in message


def test_a_client_that_declares_nothing_is_told_what_to_send() -> None:
    """Somebody driving the API with curl should be able to fix it from the error, and
    somebody on a pre-handshake client should read the upgrade line."""
    message = explain(None, "0.3.0")

    assert message is not None
    assert CLIENT_HEADER in message and "pip install" in message


def test_an_unparseable_version_still_refuses() -> None:
    """A version we cannot order is still a version that does not match -- undirected
    wording, same refusal. Guessing a direction would be worse than not offering one."""
    message = explain("main", "0.3.0")

    assert message is not None
    assert "does not match" in message


def test_the_version_is_written_in_exactly_one_place() -> None:
    """`pyproject.toml` reads it from the package. Two literals is one of them going
    stale, and this one is the compatibility contract, not a label.

    Read as text rather than parsed: `tomllib` is 3.11 and the floor is 3.10 (AGENTS.md),
    and a second dependency for one assertion is not worth it."""
    pyproject = PYPROJECT.read_text()

    assert re.search(r'(?m)^version = "', pyproject) is None
    assert 'dynamic = ["version"]' in pyproject
    assert 'attr = "relore.__version__"' in pyproject
    assert __version__ != "0.0.0"
