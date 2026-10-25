#!/usr/bin/env bash
# Pause AgentIDE on GKE to stop paying for compute, and resume it later (see
# infra/gcp/README.md, "Pausing").
#
#   PROJECT_ID=my-project bash infra/gcp/stop.sh            # pause dev and prod
#   PROJECT_ID=my-project bash infra/gcp/stop.sh resume     # bring them back
#   PROJECT_ID=my-project ENVS=dev bash infra/gcp/stop.sh   # just one env
#
# Autopilot bills the pods' resource requests, so pausing scales every
# Deployment and StatefulSet to zero and hibernates the CloudNativePG cluster.
# The cluster, volumes, secrets, IPs and Ingress stay, so nothing is lost and
# resuming takes minutes, not a re-bootstrap. Deleting the cluster would save
# only its management fee, which the GKE free tier covers for one cluster.
#
# Each scaled object keeps its old replica count in the annotation
# agentide.io/paused-replicas, which resume reads back. Both actions are safe
# to re-run.
#
# Optional env: REGION (us-central1), CLUSTER (agentide), ENVS ("dev prod").
# The shared namespaces (OpenObserve, cert-manager, the CNPG operator) are
# paused only when every env is, and resumed with any env.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID to your GCP project id}"
REGION="${REGION:-us-central1}"
CLUSTER="${CLUSTER:-agentide}"
read -r -a ENVS <<<"${ENVS:-dev prod}"

ALL_ENVS="dev prod"
PG_CLUSTER=agentide-pg  # postgres.name in infra/helm/agentide-data/values.yaml
OBSERVABILITY_NS=observability
# Resume brings these up first: the CNPG operator and its webhooks must run
# before the Postgres clusters can wake.
ADDON_NAMESPACES=(cert-manager cnpg-system)
ANNOTATION=agentide.io/paused-replicas

namespace() { echo "agentide-$1"; }

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
exists() { "$@" >/dev/null 2>&1; }

# Whether ENVS names every env.
shared_too() {
  local env
  for env in $ALL_ENVS; do
    case " ${ENVS[*]} " in *" $env "*) ;; *) return 1 ;; esac
  done
}

pg() { echo "clusters.postgresql.cnpg.io/$PG_CLUSTER"; }

# ── Workloads ────────────────────────────────────────────────────────────────

pause_kind() {  # <namespace> <deployment|statefulset>
  local ns=$1 kind=$2 name replicas
  kubectl -n "$ns" get "$kind" \
    -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.replicas}{"\n"}{end}' |
    while read -r name replicas; do
      [ -n "$name" ] && [ "${replicas:-0}" -gt 0 ] || continue
      kubectl -n "$ns" annotate "$kind/$name" "$ANNOTATION=$replicas" --overwrite >/dev/null
      kubectl -n "$ns" scale "$kind/$name" --replicas 0 >/dev/null
      echo "    $ns $kind/$name: $replicas → 0"
    done
}

resume_kind() {  # <namespace> <deployment|statefulset>
  local ns=$1 kind=$2 name replicas
  kubectl -n "$ns" get "$kind" \
    -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.metadata.annotations.agentide\.io/paused-replicas}{"\n"}{end}' |
    while read -r name replicas; do
      [ -n "$name" ] && [ -n "$replicas" ] || continue
      kubectl -n "$ns" scale "$kind/$name" --replicas "$replicas" >/dev/null
      kubectl -n "$ns" annotate "$kind/$name" "$ANNOTATION-" >/dev/null
      echo "    $ns $kind/$name: 0 → $replicas"
    done
}

wait_rollouts() {  # <namespace> <deployment|statefulset>
  local ns=$1 kind=$2 name
  for name in $(kubectl -n "$ns" get "$kind" -o name); do
    kubectl -n "$ns" rollout status "$name" --timeout 10m >/dev/null
  done
}

pause_namespace() {  # <namespace>
  # Deployments first, so nothing is writing when the data services stop.
  pause_kind "$1" deployment
  pause_kind "$1" statefulset
}

resume_namespace() {  # <namespace>
  resume_kind "$1" statefulset
  wait_rollouts "$1" statefulset
  resume_kind "$1" deployment
  wait_rollouts "$1" deployment
}

# ── Postgres (CloudNativePG declarative hibernation) ─────────────────────────

set_backups_suspended() {  # <namespace> <true|false>
  local backup
  for backup in $(kubectl -n "$1" get scheduledbackups.postgresql.cnpg.io -o name); do
    kubectl -n "$1" patch "$backup" --type merge -p "{\"spec\":{\"suspend\":$2}}" >/dev/null
  done
}

hibernate_pg() {  # <namespace>
  local ns=$1
  if ! exists kubectl -n "$ns" get "$(pg)"; then
    echo "    $ns: no $PG_CLUSTER cluster"
    return
  fi
  # A scheduled backup of a hibernated cluster would only fail.
  set_backups_suspended "$ns" true
  # Removes the instance pods and keeps their volumes.
  kubectl -n "$ns" annotate "$(pg)" cnpg.io/hibernation=on --overwrite >/dev/null
  if [ -n "$(kubectl -n "$ns" get pods -l "cnpg.io/cluster=$PG_CLUSTER" -o name)" ]; then
    kubectl -n "$ns" wait pods -l "cnpg.io/cluster=$PG_CLUSTER" --for delete --timeout 10m >/dev/null
  fi
  echo "    $ns: $PG_CLUSTER hibernated"
}

wake_pg() {  # <namespace>
  local ns=$1 tries=0
  exists kubectl -n "$ns" get "$(pg)" || return 0
  kubectl -n "$ns" annotate "$(pg)" cnpg.io/hibernation=off --overwrite >/dev/null
  # The Cluster can still report Ready from before it hibernated, so wait for
  # an instance pod to come back before trusting the condition.
  until [ -n "$(kubectl -n "$ns" get pods -l "cnpg.io/cluster=$PG_CLUSTER" -o name)" ]; do
    tries=$((tries + 1))
    if [ "$tries" -gt 60 ]; then
      echo "no $PG_CLUSTER pod in $ns after 5 minutes" >&2
      exit 1
    fi
    sleep 5
  done
  kubectl -n "$ns" wait "$(pg)" --for condition=Ready --timeout 10m >/dev/null
  set_backups_suspended "$ns" false
  echo "    $ns: $PG_CLUSTER ready"
}

# ── Actions ──────────────────────────────────────────────────────────────────

pause() {
  local env ns
  for env in "${ENVS[@]}"; do
    ns="$(namespace "$env")"
    log "Pausing $ns"
    pause_namespace "$ns"
    hibernate_pg "$ns"
  done

  if shared_too; then
    log "Pausing $OBSERVABILITY_NS and the add-ons"
    pause_namespace "$OBSERVABILITY_NS"
    # Last: the CNPG operator had to run to hibernate the clusters.
    for ns in "${ADDON_NAMESPACES[@]}"; do pause_namespace "$ns"; done
  fi

  cat <<EOF

Paused ${ENVS[*]}. Still billed while paused: the persistent disks, the static
IPs, the Ingress load balancers' forwarding rules, Cloud Storage and Artifact
Registry storage, and the cluster fee (covered by the GKE free tier for one
cluster). A deploy (push to main or a release) brings the app pods back but
not Postgres, so resume first:
  PROJECT_ID=$PROJECT_ID ENVS="${ENVS[*]}" bash infra/gcp/stop.sh resume
EOF
}

resume() {
  local env ns
  # Always, even for one env: every env needs them. Namespaces that weren't
  # paused have nothing annotated and are left as they are.
  log "Resuming the add-ons and $OBSERVABILITY_NS"
  for ns in "${ADDON_NAMESPACES[@]}"; do resume_namespace "$ns"; done
  resume_namespace "$OBSERVABILITY_NS"

  for env in "${ENVS[@]}"; do
    ns="$(namespace "$env")"
    log "Resuming $ns"
    wake_pg "$ns"
    resume_namespace "$ns"
  done
  printf '\nResumed %s.\n' "${ENVS[*]}"
}

main() {
  local env
  for env in "${ENVS[@]}"; do
    case " $ALL_ENVS " in
      *" $env "*) ;;
      *) echo "unknown env '$env'; envs: $ALL_ENVS" >&2; exit 64 ;;
    esac
  done

  gcloud --project "$PROJECT_ID" --quiet container clusters get-credentials "$CLUSTER" \
    --region "$REGION" >/dev/null

  case "${1:-pause}" in
    pause) pause ;;
    resume) resume ;;
    *) echo "usage: stop.sh [pause|resume]" >&2; exit 64 ;;
  esac
}

main "$@"
