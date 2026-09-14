"""The console-script shim: a missing extra is a sentence, not a traceback.

``pip install relore`` registers ``relored`` and deliberately does not carry what it
imports (AGENTS.md invariant 1), so this path is reached by anybody who installs the
client and types the daemon's name. It answered with a ``ModuleNotFoundError`` and the
reader's own stack, which hides the useful fact: every client verb still works
(huggingface/relore#19).
"""

from __future__ import annotations

import builtins

import pytest

from relore import entrypoints


def _without(monkeypatch: pytest.MonkeyPatch, module: str) -> None:
    """Make importing ``relore.daemon`` fail the way a client-only install fails."""
    real = builtins.__import__

    def guard(name: str, *args, **kwargs):
        if name == "relore.daemon":
            raise ModuleNotFoundError(f"No module named {module!r}", name=module)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard)


def test_a_client_only_install_is_told_which_extra_to_add(monkeypatch: pytest.MonkeyPatch) -> None:
    _without(monkeypatch, "sqlalchemy")

    with pytest.raises(SystemExit) as exc:
        entrypoints.relored()

    message = str(exc.value)
    assert "sqlalchemy" in message, "name the cause"
    assert "relore[server]" in message and "relore[postgres]" in message, "name the remedy"
    # The clause a traceback cannot carry: the session is still viable.
    assert "client verbs" in message


def test_our_own_missing_module_is_not_dressed_up_as_a_missing_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo in an internal import is a bug, and telling somebody to install an extra
    would send them to fix it in the wrong place."""
    _without(monkeypatch, "relore.ingest.typo")

    with pytest.raises(ModuleNotFoundError):
        entrypoints.relored()
