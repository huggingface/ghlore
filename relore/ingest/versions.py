"""The two counters that make ``derive`` incremental (section 5.0).

Bump the relevant one and only the affected rows are recomputed:

* ``CHUNKING_VERSION`` participates in ``content_hash``, so bumping it invalidates every
  document exactly once, on purpose.
* ``EXTRACTOR_VERSION`` gates re-derivation of the signal tables. It does *not* enter the
  hash: improving a symbol extractor must not rewrite prose that did not change.

Redaction (section 11.3) is part of the text that feeds the hash, so improving a pattern
set changes ``body_text`` and the affected rows update on their own -- but only for
threads that get re-derived, so a pattern-set change wants ``derive --full``.
"""

from __future__ import annotations

CHUNKING_VERSION = 1
EXTRACTOR_VERSION = 1
