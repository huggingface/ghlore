# Security

- The index is **read-only to every client** — the API connects with a `SELECT`-only role.
- **Repository allowlist, not denylist**, re-checked per thread, so a private repository
  cannot leak into a public answer.
- **Per-token repo scoping.** A client token cannot reach outside its set.
- **Untrusted-content envelope** on every response, server-side and never optional.
  `--compact` drops its one-line header, never the delimiters or the per-line `>` marks.
- **Secrets redacted at ingest.** Public data is not the same as safe to re-serve.
- **Bot documents are excluded from reads by default**, reachable only by asking for them
  explicitly. Returning the deployment's own agent's comment as prior discussion would make
  that agent's unreviewed output its own evidence. See [`ranking.md`](ranking.md).
- **No write path, for anyone.** Every document has a human author and a URL, so an agent's
  wrong conclusion can never become project memory the next agent retrieves as evidence.
  The single writing endpoint — the web UI's relevance labelling, which feeds the benchmark
  — appends to a JSONL file, is gated on a token scope, and is retrieved by no query. The
  property is not "nothing writes"; it is "nothing an agent produces becomes evidence".
- **A daemon with no tokens configured will not bind anything but loopback**, and refuses a
  SQLite index outright without an explicit flag: a laptop index served to a team is how
  "the ranking is bad" becomes unfalsifiable.
- **The client cannot open a database connection**, because the code to do so is not in it —
  asserted by a test over the import graph. `relore` is the binary agents get.

The GitHub token the daemon needs is `issues:read` + `pull_requests:read`. Never write.
