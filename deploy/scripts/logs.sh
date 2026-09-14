#!/usr/bin/env bash
# Logs from any relore workload, without remembering pod names.
#
# `--component` is the useful axis: the poll loop and the API fail in completely
# different ways, and a poll failure is the one that goes quiet rather than loud
# (build-plan section 5.2: the checkpoint stops at the first failure and the pass
# otherwise looks healthy).
set -euo pipefail

namespace="ghlore"
component="serve"
context=""
since="1h"
follow=0
repo=""

usage() {
  cat <<'EOF'
Usage: deploy/scripts/logs.sh [options]

Options:
  -n, --namespace NAME   default: ghlore
      --context NAME     kubectl context
  -c, --component NAME   serve | poll | migrate | backup (default: serve)
      --repo OWNER/NAME  with -c poll: which repository's poller
      --since DURATION   default: 1h
  -f, --follow           stream
  -h, --help             this
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--namespace) namespace="$2"; shift 2 ;;
    --context) context="$2"; shift 2 ;;
    -c|--component) component="$2"; shift 2 ;;
    --repo) repo="$2"; shift 2 ;;
    --since) since="$2"; shift 2 ;;
    -f|--follow) follow=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

kube=(kubectl --namespace "$namespace")
[[ -n "$context" ]] && kube+=(--context "$context")

selector="app=relore,component=${component}"
if [[ -n "$repo" ]]; then
  slug="$(echo "$repo" | tr '/.' '--' | tr '[:upper:]' '[:lower:]')"
  selector="${selector},repo=${slug}"
fi

args=(logs -l "$selector" --since "$since" --all-containers --prefix --tail=-1)
[[ $follow -eq 1 ]] && args+=(--follow)
exec "${kube[@]}" "${args[@]}"
