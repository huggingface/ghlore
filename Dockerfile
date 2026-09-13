# ghlore — the daemon image. `ghlored` and `ghlore`, nothing else.
#
# It installs `ghlore[postgres,python]`: the daemon's half of the code lens landed, so
# without a parser `symbol`, `copies` and `defs`/`refs --repo` answer `MissingParser`.
# `git` is what `ghlored clone` and `why`'s blame shell out to.
#
# `pg_isready` comes from postgresql-client and is used by the migrate hook to
# wait for the database rather than fail the release on a first-install race.
FROM python:3.10-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends git postgresql-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY ghlore ./ghlore
RUN pip install --no-cache-dir '.[postgres,python]'

# Not root. The daemon reads a token and writes one JSONL file; it has no reason
# to be able to do anything else.
RUN useradd --create-home --uid 10001 ghlore \
 && mkdir -p /var/lib/ghlore \
 && chown -R ghlore:ghlore /var/lib/ghlore
USER ghlore

# `ghlored` is the entry point, so a workload's args read as the verb they are:
# ["serve", "--host", "0.0.0.0"] rather than a full command line.
ENTRYPOINT ["ghlored"]
CMD ["--help"]
