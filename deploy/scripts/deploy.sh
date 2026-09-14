#!/usr/bin/env bash
# relore -> Kubernetes. A thin wrapper around `helm upgrade --install`.
#
# The script is the interface (see the playbooks repo's CLAUDE.md): it encodes the
# context check, the values requirement and the post-deploy verification, so that a
# human can run the whole playbook by hand and get the same result.
#
# Three things it refuses, each because of something that has already gone wrong
# somewhere in this stack:
#   * deploying without `-f`. The production values are NOT in this repository,
#     and `values.yaml` here is the chart's shape, not an environment. On
#     2026-09-04 a serge deploy used the clone's own copy: helm said "Upgrade
#     complete", the pod stayed 1/1 Running, and three settings were silently
#     unset for four days.
#   * deploying against the wrong cluster, whenever --context is given.
#   * deploying values that DROP a key the live release has, unless --allow-removals.
#     That is the same guard serge grew after the incident above.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHART_DIR="${ROOT_DIR}/deploy/helm"

# Both stay `ghlore` after the 2026-09-14 rename to relore. The release name is what
# every resource in the chart is named after (`_helpers.tpl`'s "name" is `.Release.Name`),
# and the namespace holds the PVCs -- so renaming either does not rename the deployment,
# it stands up a second, empty one beside it. The project is relore; this deployment is
# still called ghlore, and so is its hostname.
release="ghlore"
namespace="ghlore"
values_files=()
expected_context=""
dry_run=0
plan=0
allow_removals=0
timeout="10m"

usage() {
  cat <<'EOF'
Usage: deploy/scripts/deploy.sh [options]

Options:
  -n, --namespace NAME    Kubernetes namespace (default: ghlore)
  -r, --release NAME      Helm release name (default: ghlore)
  -f, --values FILE       Helm values file; repeat to layer, later files win.
                          REQUIRED — production values are not in this repo.
      --context NAME      Require this kubectl context before doing anything
      --plan              Show what would change against the live release, then stop
      --dry-run           Render the full manifest without applying
      --allow-removals    Permit values that drop keys the live release has
      --timeout DURATION  Helm timeout (default: 10m)
  -h, --help              This

Examples:
  deploy/scripts/deploy.sh --plan -f ../env/prod.yaml
  deploy/scripts/deploy.sh --context infra:opensource-aws-use1-prod-54 \
    -n ghlore -f ../env/prod.yaml
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--namespace) namespace="$2"; shift 2 ;;
    -r|--release) release="$2"; shift 2 ;;
    -f|--values) values_files+=("$2"); shift 2 ;;
    --context) expected_context="$2"; shift 2 ;;
    --plan) plan=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    --allow-removals) allow_removals=1; shift ;;
    --timeout) timeout="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ${#values_files[@]} -eq 0 ]]; then
  echo "error: -f/--values is required." >&2
  echo "  deploy/helm/values.yaml is the chart's shape, not an environment." >&2
  echo "  Pass the tracked production values, e.g. -f ../env/prod.yaml" >&2
  exit 2
fi

for f in "${values_files[@]}"; do
  [[ -f "$f" ]] || { echo "error: values file not found: $f" >&2; exit 2; }
done

if [[ -n "$expected_context" ]]; then
  current="$(kubectl config current-context 2>/dev/null || true)"
  if [[ "$current" != "$expected_context" ]]; then
    echo "error: kubectl context is '${current:-<none>}', expected '$expected_context'" >&2
    exit 2
  fi
fi

# --- the namespace has to exist, and creating one is a cluster-scoped right ----
# Most operators here have full rights *inside* a namespace and none to create one,
# so `--create-namespace` fails with a `cannot patch resource "namespaces"` error
# that reads like a bug in this script. Say what it actually is.
# Passed ONLY when the namespace is genuinely missing. Helm 4 server-side applies
# the Namespace object whenever the flag is present, including when it already
# exists -- which needs `patch` on namespaces, a cluster-scoped right most
# operators here do not have. So the flag turns a working deploy into a permission
# error on every run after the first.
create_namespace=()
if ! kubectl ${expected_context:+--context "$expected_context"} \
     get namespace "$namespace" >/dev/null 2>&1; then
  create_namespace=(--create-namespace)
  if [[ "$(kubectl ${expected_context:+--context "$expected_context"} \
           auth can-i create namespaces 2>/dev/null)" != "yes" ]]; then
    cat >&2 <<MSG
error: namespace '$namespace' does not exist, and this account cannot create one.
  Creating a namespace is a cluster-scoped right; namespaced rights inside it are
  usually already granted, so this is the only step that needs someone else:

    kubectl --context ${expected_context:-<context>} create namespace $namespace

  Ask whoever administers the cluster to run that, then re-run this script.
MSG
    exit 4
  fi
fi

# `${a[@]+"${a[@]}"}`, not `"${a[@]}"`: under `set -u`, bash 3.2 -- which is what
# macOS ships -- treats an empty array's expansion as an unbound variable.
kube_ctx=()
[[ -n "$expected_context" ]] && kube_ctx=(--context "$expected_context")

helm_args=(upgrade --install "$release" "$CHART_DIR" --namespace "$namespace" \
           ${create_namespace[@]+"${create_namespace[@]}"})
for f in "${values_files[@]}"; do helm_args+=(--values "$f"); done
[[ -n "$expected_context" ]] && helm_args+=(--kube-context "$expected_context")

if [[ $dry_run -eq 1 ]]; then
  exec helm "${helm_args[@]}" --dry-run --debug
fi

# --- what this deploy would remove ------------------------------------------
# A values file that drops a key does not fail: helm falls back to the chart default
# and the workload comes back healthy with the setting silently gone. So diff the
# keys, not the outcome.
if helm status "$release" --namespace "$namespace" \
     ${expected_context:+--kube-context "$expected_context"} >/dev/null 2>&1; then
  live="$(helm get values "$release" --namespace "$namespace" \
    ${expected_context:+--kube-context "$expected_context"} --output json 2>/dev/null || true)"
  proposed="$(helm "${helm_args[@]}" --dry-run --output json 2>/dev/null \
    | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin).get("config") or {}))' 2>/dev/null || true)"

  # An empty result means "could not determine", NEVER "everything was removed".
  # The difference matters: a dry-run fails for reasons that have nothing to do with
  # the values -- a release stuck in a failed state, a transient API error -- and a
  # guard that reads its own failure as a diff refuses every legitimate deploy while
  # sounding maximally alarming. That happened once; this is the fix.
  if [[ -z "$live" || "$live" == "null" || -z "$proposed" || "$proposed" == "{}" ]]; then
    echo "note: could not compare values against the live release; removal check skipped." >&2
  else
    removed="$(python3 - "$live" "$proposed" <<'PYEOF'
import json, sys

def flatten(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from flatten(value, f"{prefix}.{key}" if prefix else key)
    else:
        yield prefix

live, proposed = (json.loads(a or "{}") for a in sys.argv[1:3])
print("\n".join(sorted(set(flatten(live)) - set(flatten(proposed)))))
PYEOF
)"
    if [[ -n "$removed" ]]; then
      echo "This deploy DROPS settings the live release has:" >&2
      echo "$removed" | sed 's/^/  - /' >&2
      if [[ $allow_removals -eq 0 ]]; then
        echo "Refusing. Re-run with --allow-removals if that is intended." >&2
        exit 3
      fi
      echo "Continuing because --allow-removals was given." >&2
    fi
  fi
fi

if [[ $plan -eq 1 ]]; then
  echo "release   $release (namespace $namespace)"
  echo "context   ${expected_context:-$(kubectl config current-context 2>/dev/null || echo '<none>')}"
  echo "values    ${values_files[*]}"
  echo "chart     $CHART_DIR"
  echo
  echo "Workloads this chart manages:"
  helm "${helm_args[@]}" --dry-run 2>/dev/null \
    | grep -E "^kind:|^  name:" | paste - - | sed 's/kind: //; s/  name: //' | sed 's/^/  /'
  echo
  echo "No changes applied. Drop --plan to deploy."
  exit 0
fi

# No `--wait`. Helm's wait blocks until every resource is "ready", and that
# includes PersistentVolumeClaims -- but this cluster's StorageClass binds
# WaitForFirstConsumer, so the backup PVC stays Pending until the nightly CronJob
# first runs. That is correct behaviour for the PVC and a failed release for helm:
# the install rolled back twice on a volume that was doing exactly what it should.
#
# So wait for the things whose readiness actually means something, by name.
helm "${helm_args[@]}" --timeout "$timeout"

echo
for workload in "deploy/${release}" "statefulset/${release}-postgres"; do
  kubectl "${kube_ctx[@]}" --namespace "$namespace" rollout status "$workload" \
    --timeout="$timeout" || exit 1
done
for d in $(kubectl "${kube_ctx[@]}" --namespace "$namespace" get deploy \
             -l "app=relore" -o name 2>/dev/null | grep -- "-poll-"); do
  kubectl "${kube_ctx[@]}" --namespace "$namespace" rollout status "$d" --timeout="$timeout" || exit 1
done

# --- verify, because "Upgrade complete" is not verification -----------------
kube=(kubectl --namespace "$namespace")
[[ -n "$expected_context" ]] && kube+=(--context "$expected_context")


echo
echo "== pods =="
"${kube[@]}" get pods -l "app=relore" -o wide
echo
echo "== index status =="
# From inside the cluster, through the daemon's own reporting rather than by
# querying the database: this is the same output a human gets from `relored
# status`, and it prints the sample window, the per-pass high-water marks and the
# superseded-document count (section 14.1).
"${kube[@]}" exec "deploy/${release}" -- relored status || {
  echo "warning: could not read status from the serve pod" >&2
}
