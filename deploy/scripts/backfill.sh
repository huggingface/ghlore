#!/usr/bin/env bash
# `relored backfill` as a Kubernetes Job. A thin wrapper: the mechanism is in
# `pass.sh`, which runs any long pass the same way.
#
# Kept as its own name because the playbook and the build plan both point at it,
# and because a backfill is the one pass whose *options* are worth documenting
# separately -- see `pass.sh --help` and section 3.
set -euo pipefail
exec env RELORED_PASS=backfill "$(dirname "$0")/pass.sh" "$@"
