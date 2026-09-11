# Operations

Running the daemon needs a Postgres and a GitHub token with `issues:read` +
`pull_requests:read` — never write. `deploy/` has a Helm chart and the scripts that are its
interface.

## Install

```bash
GH=git+https://github.com/huggingface/ghlore   # no PyPI release yet; install from main
pip install $GH                        # client only
pip install "ghlore[postgres] @ $GH"   # the daemon
```

The client must be the same version as the daemon it talks to; if it is not, the daemon
refuses the request and says which end is behind. See [One version, both
ends](#one-version-both-ends).

## Index

```bash
export GHLORE_DATABASE_URL=postgresql://localhost/ghlore   # or sqlite:///ghlore.db, dev only
export GITHUB_TOKEN=...                                    # issues:read + pull_requests:read
ghlored migrate
ghlored backfill --repo owner/name           # full history; resumable, ~a day
ghlored poll     --repo owner/name --interval 5m
ghlored sweep    --repo owner/name           # weekly: reconcile deletions
```

Run **`ghlored authority`** after backfilling an org-owned repository, or `--trust
authoritative` returns nothing: maintainers whose write access comes through a team report
as `MEMBER` and stay in `reported` until it does. Measured before that pass existed: zero
of 415 documents were `authoritative`.

`ghlored sample --repo … --since YYYY-MM-DD --kind issue|pr [--merged]` indexes a bounded
window instead of a history, fetching **each thread whole** — §10's evaluation set needs a
corpus, not a day of API budget. It is deliberately not `backfill --since`: that would walk
`/issues/comments?since=`, which filters individual comments and would index a thread that
moved inside the window without the older comments that explain it (§5.5). A sampled index
declares its floor and `ghlore status` prints it, because a recall number is only comparable
against a baseline restricted to the same window.

`fetch` and `derive` are separate on purpose: raw payloads are staged, so improving an
extractor and re-deriving costs minutes of local CPU instead of another day of API budget.
`poll` does both for the threads that moved, so the split is invisible in steady state.

## Serve

One token per consumer, each scoped to repositories — `secret:repos[:scopes]`, where
`repos` is a comma list or `*` and `scopes` adds `label` for the UI:

```bash
export GHLORE_API_TOKENS="tok_agent:owner/name tok_ui:*:label"
export GHLORE_LABELS_PATH=./labels.jsonl     # enables the UI's relevance labelling
ghlored serve --port 8080 --host 0.0.0.0
```

Or no tokens at all, when a private network is the perimeter: `serve --trust-network` binds
wide with no tokens, which is how the deployment runs. Without either, **serve refuses to
bind anything but loopback** — the default fails closed — and it refuses a SQLite index
outright unless you pass `--allow-sqlite`.

## Point a client at it

`ghlore` defaults to the deployment, `https://ghlore.huggingface.tech`, which is reachable
over the private network only and needs no token — the network is the perimeter there, and
responses are read-only over public GitHub history. Set `GHLORE_API` to use your own daemon
instead.

```bash
export GHLORE_API=http://localhost:8080
export GHLORE_TOKEN=tok_agent                # only if that daemon requires one
```

`--json` for machine-readable output, `--compact` to trim snippets. **No results is exit 0
with an empty result** — never nonzero, so an agent cannot mistake "nothing in the index"
for "the tool is broken".

## One version, both ends

The client and the daemon must be the **same** version, and they enforce it. Every request
to `/api/v1` declares its client version in `x-ghlore-client`; a daemon that reads anything
else answers `426 Upgrade Required` and one sentence saying which end is behind. Every
response carries `x-ghlore-version`, so a client whose daemon is too old to enforce that
catches the same mismatch from its side.

The reason is the failure mode, not tidiness: an old client asking a new daemon gets an
answer where every field it knows about is present and correct, and whatever the newer
version would have added is simply absent. Well-formed, plausible, silently incomplete — the shape of every defect in §13.3.
A refusal is legible; a short answer is not.

The price is that `ghlore/__init__.py`'s `__version__` is a contract rather than a label:

* it is the **only** place the version is written (`pyproject.toml` reads it from there);
* **bump it in the same commit** as any change a client can see — a wire payload, a
  renderer, a CLI flag; and
* **bump and deploy are one operation.** A bump merged to `main` and not shipped breaks
  every client installed after the merge, and they will be told the deployment is behind.

`ghlore --version`, `ghlored --version`, `ghlore status` and the daemon's page all print
it, so "which version is this" never needs a guess. What the mismatch looks like from the
CLI, and which end to fix, is in [`cli.md`](cli.md).

## Benchmark

```bash
# §10's benchmark: mine candidates, fold the UI's labels in, score the baselines
ghlored mine  --repo owner/name --out benchmarks/owner-name.jsonl
ghlored judge --set benchmarks/owner-name.jsonl --labels labels.jsonl
ghlored bench --repo owner/name --set benchmarks/owner-name.jsonl \
  --system index --system index+expand --system github
```

`--system grep --clone <checkout>` adds the second baseline; it reads a working tree, so
point it at a detached worktree of `origin/main`, not a branch someone is working on.
Results and caveats: [`ranking.md`](ranking.md).
