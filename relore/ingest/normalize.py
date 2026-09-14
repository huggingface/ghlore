"""Markdown normalization and secret redaction.

Two separate jobs on the way out of ``raw_objects``:

* :func:`redact` runs on the markdown **before** anything else, and its output is stored
  in *both* ``body_markdown`` and ``body_text`` -- the markdown copy is what gets shown
  (section 11.3).
* :func:`normalize` produces ``body_text``, which feeds full-text search.
"""

from __future__ import annotations

import re
from collections import Counter

from relore.security.untrusted import strip_hidden

# High-confidence patterns only. A greedy entropy heuristic over a corpus of tracebacks
# and tensor dumps produces mostly false positives -- a known failure mode of secret
# scanners on this kind of text (section 11.3). Every pattern here is anchored on a
# provider prefix or a structural marker, never on "looks random".
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private-key",
        re.compile(
            r"-----BEGIN[A-Z ]*PRIVATE KEY-----.*?-----END[A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b")),
    ("hf-token", re.compile(r"\bhf_[A-Za-z0-9]{34,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("aws-key-id", re.compile(r"\b(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    # Anthropic before OpenAI: `sk-ant-...` also matches the more general `sk-` shape.
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
)

# scheme://user:secret@host -- keep the scheme, user and host so the sentence still reads
# and still matches on everything else; replace only the password.
_URL_CREDENTIALS = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]*)://([^\s:/@]+):([^\s/@]+)@")

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2028\u2029\ufeff]")
_TRAILING_WS = re.compile(r"[ \t]+$", re.MULTILINE)
_BLANK_RUN = re.compile(r"\n{3,}")


def redact(markdown: str) -> tuple[str, Counter[str]]:
    """Replace credentials with typed placeholders.

    Returns the redacted text and a count **per pattern name**. The matched value is
    never returned, logged, or used as a metric label.

    This is detection, not a guarantee: it is worth doing because the common cases are
    mechanical and cheap to catch, not because it can be complete.
    """
    found: Counter[str] = Counter()
    # The serve path drops invisible and control characters without a placeholder, so
    # the text a reader gets has them gone: a secret split by one would miss the match
    # here and be reassembled downstream. Strip first, on the same set, then match.
    out = strip_hidden(markdown)

    for name, pattern in _SECRET_PATTERNS:

        def repl(_m: re.Match[str], _name: str = name) -> str:
            found[_name] += 1
            return f"[REDACTED:{_name}]"

        out = pattern.sub(repl, out)

    def _url_repl(m: re.Match[str]) -> str:
        found["url-credentials"] += 1
        return f"{m.group(1)}://{m.group(2)}:[REDACTED:url-credentials]@"

    out = _URL_CREDENTIALS.sub(_url_repl, out)
    return out, found


def normalize(markdown: str) -> str:
    """Produce the ``body_text`` that feeds full-text search.

    Deliberately conservative. Code spans and fenced blocks are **kept**: for technical
    retrieval the discriminating token is usually an exact identifier, and it lives in
    them. Quoted replies are kept too -- whether quote-heavy threads dilute ranking is a
    milestone 3 question to measure, not to pre-empt here.
    """
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    text = _ZERO_WIDTH.sub("", text)
    text = _HTML_COMMENT.sub("", text)
    text = _IMAGE.sub(lambda m: m.group(1), text)
    # Keep the visible text, drop the URL -- a URL contributes tokens that match nothing
    # anyone searches for. Bare links (no text) keep the target so the reference survives.
    text = _LINK.sub(lambda m: m.group(1) or m.group(2), text)
    text = _TRAILING_WS.sub("", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()
