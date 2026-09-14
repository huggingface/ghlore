"""Redaction (section 11.3) and markdown normalization."""

from __future__ import annotations

from relore.ingest.normalize import normalize, redact


def test_provider_prefixed_tokens_are_replaced_with_a_typed_placeholder() -> None:
    body = "my token is ghp_" + "a" * 36 + " ok?"
    clean, found = redact(body)
    assert "ghp_" not in clean
    assert "[REDACTED:github-token]" in clean
    assert found == {"github-token": 1}


def test_the_matched_value_is_never_returned() -> None:
    secret = "hf_" + "b" * 40
    _clean, found = redact(f"token: {secret}")
    assert all(secret not in str(key) for key in found)
    assert list(found) == ["hf-token"]


def test_private_key_blocks_go_whole() -> None:
    body = "-----BEGIN RSA PRIVATE KEY-----\nAAAA\nBBBB\n-----END RSA PRIVATE KEY-----"
    clean, found = redact(body)
    assert clean == "[REDACTED:private-key]"
    assert found == {"private-key": 1}


def test_url_credentials_keep_the_sentence_readable() -> None:
    clean, found = redact("postgresql://app:hunter2@db.internal/relore")
    assert clean == "postgresql://app:[REDACTED:url-credentials]@db.internal/relore"
    assert found == {"url-credentials": 1}
    assert "hunter2" not in clean


def test_anthropic_keys_are_not_mislabelled_as_openai() -> None:
    """`sk-ant-...` also matches the more general `sk-` shape, so order matters."""
    _clean, found = redact("sk-ant-api03-" + "c" * 30)
    assert list(found) == ["anthropic-key"]


def test_nothing_to_redact_is_a_no_op() -> None:
    body = "AttributeError: 'NoneType' object has no attribute 'dtype'"
    clean, found = redact(body)
    assert clean == body
    assert found == {}


def test_a_secret_split_by_an_invisible_byte_is_still_redacted() -> None:
    """Serve drops these bytes without a placeholder, so a token broken by one is
    reassembled downstream unless the match runs on the text the reader gets.

    The last two are line separators, which `scrub` does *not* drop -- `normalize` does,
    on the way to ``body_text``. The set that has to be stripped before matching is
    "every byte some layer removes", not "every byte this layer removes", and the
    difference was a redaction that missed `ghp_` + U+2028 + 36 characters entirely.
    """
    secret = "ghp_" + "b" * 36
    for glue in ("\u200b", "\u202e", "\x00", "\u2028", "\u2029"):
        split = secret[:4] + glue + secret[4:]
        clean, found = redact(split)
        assert secret not in clean
        assert "[REDACTED:github-token]" in clean
        assert found == {"github-token": 1}
        # The column full-text search matches and snippets are served from is the one
        # that reassembled it, so assert on that rather than only on `redact`'s output.
        assert secret not in normalize(clean)


def test_normalize_strips_html_comments_and_link_targets() -> None:
    body = "<!-- bot metadata -->see [the docs](https://example.com/x) for more"
    assert normalize(body) == "see the docs for more"


def test_normalize_keeps_a_bare_link_target() -> None:
    assert normalize("[](https://example.com/x)") == "https://example.com/x"


def test_normalize_keeps_identifiers_and_code_fences() -> None:
    """Code spans are where the discriminating token lives -- never strip them."""
    body = "call `_prepare_4d_causal_attention_mask`\n\n```py\ndef forward(self):\n    pass\n```"
    out = normalize(body)
    assert "_prepare_4d_causal_attention_mask" in out
    assert "def forward(self):" in out


def test_normalize_collapses_blank_runs_and_trailing_space() -> None:
    assert normalize("a   \n\n\n\n\nb") == "a\n\nb"


def test_normalize_drops_zero_width_characters() -> None:
    assert normalize("hel​lo") == "hello"


def test_a_bare_postgresql_url_is_pointed_at_psycopg3() -> None:
    """SQLAlchemy resolves `postgresql://` to psycopg2, which this project does not ship.

    Without this the URL printed in the README fails with `ModuleNotFoundError: psycopg2`,
    which tells the reader nothing about what to do.
    """
    from relore.store.dialect import normalize_url

    assert normalize_url("postgresql://host/db") == "postgresql+psycopg://host/db"
    assert normalize_url("postgres://host/db") == "postgresql+psycopg://host/db"
    # An explicit driver, and every non-Postgres URL, is left exactly alone.
    assert normalize_url("postgresql+psycopg2://host/db") == "postgresql+psycopg2://host/db"
    assert normalize_url("sqlite:///x.db") == "sqlite:///x.db"
