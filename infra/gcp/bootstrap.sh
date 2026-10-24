#!/usr/bin/env bash
# One-time GCP and cluster setup for AgentIDE (see infra/gcp/README.md).
#
# Run from any directory, in Cloud Shell or a shell with gcloud, kubectl (plus
# gke-gcloud-auth-plugin), helm and openssl:
#
#   PROJECT_ID=my-project bash infra/gcp/bootstrap.sh          # every step, in order
#   PROJECT_ID=my-project bash infra/gcp/bootstrap.sh data     # just the named steps
#
# Steps: apis cluster registry network buckets identity github addons
#        observability data summary
#
# Every step is safe to re-run: it creates what's missing and leaves existing
# resources (and generated passwords) as they are.
#
# Optional env: REGION (us-central1), CLUSTER (agentide), GITHUB_REPO
# (mohitpg/AgentIDE), OPENOBSERVE_EMAIL / OPENOBSERVE_PASSWORD (the OpenObserve
# root user; the password is generated if unset), and chart versions to pin:
# CERT_MANAGER_VERSION, CNPG_CHART_VERSION, BARMAN_PLUGIN_CHART_VERSION,
# OPENOBSERVE_CHART_VERSION (unset = the latest chart).
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID to your GCP project id}"
REGION="${REGION:-us-central1}"
CLUSTER="${CLUSTER:-agentide}"
GITHUB_REPO="${GITHUB_REPO:-mohitpg/AgentIDE}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENVS=(dev prod)
AR_REPO=agentide
WIF_POOL=github
WIF_PROVIDER=agentide
DEPLOYER_SA="gh-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
OBSERVABILITY_NS=observability

# Names shared with the Helm charts' defaults (infra/helm/agentide*/values.yaml).
namespace() { echo "agentide-$1"; }
ip_name() { echo "agentide-$1-ip"; }
uploads_bucket() { echo "${PROJECT_ID}-agentide-uploads-$1"; }
backup_bucket() { echo "${PROJECT_ID}-agentide-pg-backups-$1"; }
APP_KSA=agentide    # the app chart's serviceAccount.name (api + worker pods)
PG_KSA=agentide-pg  # CNPG names the instances' ServiceAccount after the Cluster

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
skip() { printf '    %s already exists\n' "$*"; }
# `exists <command...>`: whether a describe/get command succeeds.
exists() { "$@" >/dev/null 2>&1; }

gc() { gcloud --project "$PROJECT_ID" --quiet "$@"; }

PROJECT_NUMBER=""
load_project_number() {
  [ -n "$PROJECT_NUMBER" ] ||
    PROJECT_NUMBER="$(gc projects describe "$PROJECT_ID" --format='value(projectNumber)')"
}

# The Workload Identity principal of a Kubernetes ServiceAccount in this
# project's clusters. IAM grants to it need no Google service account.
ksa_principal() {  # <namespace> <ksa>
  echo "principal://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${PROJECT_ID}.svc.id.goog/subject/ns/$1/sa/$2"
}

kube_credentials() {
  gc container clusters get-credentials "$CLUSTER" --region "$REGION" >/dev/null
}

create_namespace() {
  kubectl create namespace "$1" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
}

# ── Steps ────────────────────────────────────────────────────────────────────

step_apis() {
  log "Enabling APIs"
  gc services enable \
    container.googleapis.com \
    artifactregistry.googleapis.com \
    compute.googleapis.com \
    storage.googleapis.com \
    iam.googleapis.com \
    iamcredentials.googleapis.com \
    sts.googleapis.com \
    cloudresourcemanager.googleapis.com
}

step_cluster() {
  log "GKE Autopilot cluster $CLUSTER ($REGION)"
  # Autopilot has Workload Identity and the Cloud Storage FUSE CSI driver on
  # by default.
  if exists gc container clusters describe "$CLUSTER" --region "$REGION"; then
    skip "cluster $CLUSTER"
  else
    gc container clusters create-auto "$CLUSTER" --region "$REGION" --release-channel regular
  fi
  kube_credentials
  for env in "${ENVS[@]}"; do create_namespace "$(namespace "$env")"; done
  create_namespace "$OBSERVABILITY_NS"
}

step_registry() {
  log "Artifact Registry repository $AR_REPO"
  if exists gc artifacts repositories describe "$AR_REPO" --location "$REGION"; then
    skip "repository $AR_REPO"
  else
    gc artifacts repositories create "$AR_REPO" --location "$REGION" \
      --repository-format docker --description "AgentIDE images"
  fi

  # Every push to main pushes seven images (the torch ones are large). Keep
  # release versions (v*) and the 10 newest of each image, delete the rest of
  # the sha-* builds after 30 days, and untagged leftovers after 7.
  local policy
  policy="$(mktemp)"
  cat >"$policy" <<'EOF'
[
  {"name": "keep-releases", "action": {"type": "Keep"},
   "condition": {"tagState": "TAGGED", "tagPrefixes": ["v"]}},
  {"name": "keep-recent", "action": {"type": "Keep"},
   "mostRecentVersions": {"keepCount": 10}},
  {"name": "delete-old-builds", "action": {"type": "Delete"},
   "condition": {"tagState": "TAGGED", "tagPrefixes": ["sha-"], "olderThan": "30d"}},
  {"name": "delete-untagged", "action": {"type": "Delete"},
   "condition": {"tagState": "UNTAGGED", "olderThan": "7d"}}
]
EOF
  gc artifacts repositories set-cleanup-policies "$AR_REPO" --location "$REGION" \
    --policy "$policy" --no-dry-run >/dev/null
  rm -f "$policy"
}

step_network() {
  log "Global static IPs"
  for env in "${ENVS[@]}"; do
    local name
    name="$(ip_name "$env")"
    if exists gc compute addresses describe "$name" --global; then
      skip "address $name"
    else
      gc compute addresses create "$name" --global --ip-version IPV4
    fi
  done
}

create_bucket() {
  if exists gc storage buckets describe "gs://$1"; then
    skip "bucket gs://$1"
  else
    gc storage buckets create "gs://$1" --location "$REGION" \
      --uniform-bucket-level-access --public-access-prevention
  fi
}

step_buckets() {
  log "Cloud Storage buckets"
  for env in "${ENVS[@]}"; do
    create_bucket "$(uploads_bucket "$env")"
    # One backup bucket per env, so dev's (less trusted) code can't read prod
    # backups.
    create_bucket "$(backup_bucket "$env")"
  done
}

bucket_binding() {  # <bucket> <member> <role>
  gc storage buckets add-iam-policy-binding "gs://$1" --member "$2" --role "$3" >/dev/null
}

step_identity() {
  log "Workload Identity grants on the buckets"
  load_project_number
  for env in "${ENVS[@]}"; do
    local ns
    ns="$(namespace "$env")"
    # api + worker pods: read/write uploads through GCS FUSE.
    bucket_binding "$(uploads_bucket "$env")" "$(ksa_principal "$ns" "$APP_KSA")" roles/storage.objectUser
    # Postgres instances: Barman Cloud writes and prunes backups, and checks
    # the bucket exists (storage.buckets.get).
    bucket_binding "$(backup_bucket "$env")" "$(ksa_principal "$ns" "$PG_KSA")" roles/storage.objectAdmin
    bucket_binding "$(backup_bucket "$env")" "$(ksa_principal "$ns" "$PG_KSA")" roles/storage.legacyBucketReader
    echo "    $ns: $APP_KSA → $(uploads_bucket "$env"), $PG_KSA → $(backup_bucket "$env")"
  done
}

step_github() {
  log "GitHub Actions Workload Identity Federation ($GITHUB_REPO)"
  load_project_number
  if exists gc iam workload-identity-pools describe "$WIF_POOL" --location global; then
    skip "pool $WIF_POOL"
  else
    gc iam workload-identity-pools create "$WIF_POOL" --location global \
      --display-name "GitHub Actions"
  fi

  # Only tokens issued for this repository are accepted at all.
  if exists gc iam workload-identity-pools providers describe "$WIF_PROVIDER" \
      --location global --workload-identity-pool "$WIF_POOL"; then
    skip "provider $WIF_PROVIDER"
  else
    gc iam workload-identity-pools providers create-oidc "$WIF_PROVIDER" \
      --location global --workload-identity-pool "$WIF_POOL" \
      --display-name "AgentIDE repository" \
      --issuer-uri "https://token.actions.githubusercontent.com" \
      --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
      --attribute-condition "assertion.repository == '${GITHUB_REPO}'"
  fi

  if exists gc iam service-accounts describe "$DEPLOYER_SA"; then
    skip "service account $DEPLOYER_SA"
  else
    gc iam service-accounts create gh-deployer --display-name "GitHub Actions deployer"
  fi

  # Push images (and retag them on release) ...
  gc artifacts repositories add-iam-policy-binding "$AR_REPO" --location "$REGION" \
    --member "serviceAccount:$DEPLOYER_SA" --role roles/artifactregistry.writer >/dev/null
  # ... and deploy to the cluster's namespaces (no RBAC or cluster admin).
  gc projects add-iam-policy-binding "$PROJECT_ID" \
    --member "serviceAccount:$DEPLOYER_SA" --role roles/container.developer \
    --condition None >/dev/null
  # Workflows in the repository can act as the deployer.
  gc iam service-accounts add-iam-policy-binding "$DEPLOYER_SA" \
    --role roles/iam.workloadIdentityUser \
    --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/attribute.repository/${GITHUB_REPO}" \
    >/dev/null
}

step_addons() {
  log "cert-manager, CloudNativePG and the Barman Cloud plugin"
  kube_credentials
  helm repo add jetstack https://charts.jetstack.io --force-update >/dev/null
  helm repo add cnpg https://cloudnative-pg.github.io/charts --force-update >/dev/null
  helm repo update >/dev/null

  # The Barman Cloud plugin's TLS comes from cert-manager. Autopilot doesn't
  # allow writes to kube-system, where cert-manager keeps its leader-election
  # lease by default.
  helm upgrade --install cert-manager jetstack/cert-manager \
    ${CERT_MANAGER_VERSION:+--version "$CERT_MANAGER_VERSION"} \
    --namespace cert-manager --create-namespace \
    --set crds.enabled=true \
    --set global.leaderElection.namespace=cert-manager \
    --wait --timeout 10m

  helm upgrade --install cnpg cnpg/cloudnative-pg \
    ${CNPG_CHART_VERSION:+--version "$CNPG_CHART_VERSION"} \
    --namespace cnpg-system --create-namespace \
    --wait --timeout 10m

  # Must live in the operator's namespace.
  helm upgrade --install plugin-barman-cloud cnpg/plugin-barman-cloud \
    ${BARMAN_PLUGIN_CHART_VERSION:+--version "$BARMAN_PLUGIN_CHART_VERSION"} \
    --namespace cnpg-system \
    --wait --timeout 10m
}

step_observability() {
  log "OpenObserve in $OBSERVABILITY_NS"
  kube_credentials
  create_namespace "$OBSERVABILITY_NS"
  helm repo add openobserve https://charts.openobserve.ai --force-update >/dev/null
  helm repo update openobserve >/dev/null

  # The root user is created from this secret on first start. The suffix
  # covers OpenObserve's password rules (upper, lower, digit, symbol).
  if exists kubectl -n "$OBSERVABILITY_NS" get secret openobserve-root; then
    skip "secret openobserve-root"
  else
    kubectl -n "$OBSERVABILITY_NS" create secret generic openobserve-root \
      --from-literal ZO_ROOT_USER_EMAIL="${OPENOBSERVE_EMAIL:-root@agentide.local}" \
      --from-literal ZO_ROOT_USER_PASSWORD="${OPENOBSERVE_PASSWORD:-$(openssl rand -hex 16)-Aa1}"
  fi

  # fullnameOverride makes the Service openobserve.observability:5080, the
  # app chart's default OTLP endpoint. Each env exports to its own org
  # (/api/dev, /api/prod), which OpenObserve creates on first ingest.
  helm upgrade --install openobserve openobserve/openobserve-standalone \
    ${OPENOBSERVE_CHART_VERSION:+--version "$OPENOBSERVE_CHART_VERSION"} \
    --namespace "$OBSERVABILITY_NS" \
    --set fullnameOverride=openobserve \
    --set auth.existingRootUserSecret.name=openobserve-root \
    --set persistence.size=20Gi \
    --set resources.requests.cpu=500m \
    --set resources.requests.memory=1Gi \
    --set resources.limits.memory=1Gi \
    --wait --timeout 10m
}

step_data() {
  log "Data services (infra/helm/agentide-data)"
  kube_credentials
  local chart="$REPO_ROOT/infra/helm/agentide-data"
  for env in "${ENVS[@]}"; do
    local ns
    ns="$(namespace "$env")"
    create_namespace "$ns"

    # RabbitMQ only reads the password when its volume is empty, so this is
    # generated once and never replaced. The app chart reads the same secret.
    if exists kubectl -n "$ns" get secret agentide-rabbitmq; then
      skip "secret $ns/agentide-rabbitmq"
    else
      kubectl -n "$ns" create secret generic agentide-rabbitmq \
        --from-literal password="$(openssl rand -hex 24)"
    fi

    helm upgrade --install agentide-data "$chart" \
      --namespace "$ns" \
      -f "$chart/values-$env.yaml" \
      --set postgres.backup.bucket="$(backup_bucket "$env")" \
      --wait --timeout 10m
    # Helm's --wait doesn't know CNPG's Cluster kind.
    kubectl -n "$ns" wait clusters.postgresql.cnpg.io/agentide-pg --for condition=Ready --timeout 10m
  done
}

step_summary() {
  log "GitHub setup"
  load_project_number
  local provider="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/providers/${WIF_PROVIDER}"
  cat <<EOF
Repository or environment variables (Settings → Secrets and variables → Actions):
  GCP_PROJECT   = $PROJECT_ID
  GCP_REGION    = $REGION
  WIF_PROVIDER  = $provider
  DEPLOYER_SA   = $DEPLOYER_SA

Per-environment API_HOST (environments "dev" and "production"):
EOF
  local env ip host
  for env in "${ENVS[@]}"; do
    ip="$(gc compute addresses describe "$(ip_name "$env")" --global --format 'value(address)' 2>/dev/null || true)"
    if [ -z "$ip" ]; then
      echo "  $env: no static IP yet (run the network step)"
      continue
    fi
    if [ "$env" = prod ]; then host="api.${ip//./-}.sslip.io"; else host="dev-api.${ip//./-}.sslip.io"; fi
    echo "  $env: API_HOST = $host   ($(ip_name "$env") = $ip)"
  done
  cat <<'EOF'

Environment secrets (both environments): SECRET_KEY, A2A_AUTH_TOKEN,
OPENROUTER_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, SERPER_API_KEY,
GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, OPENOBSERVE_AUTH_TOKEN.
RabbitMQ's password stays in the cluster (secret agentide-rabbitmq).

OPENOBSERVE_AUTH_TOKEN is base64("email:password") of the OpenObserve user;
for the root user, print it with:
  kubectl -n observability get secret openobserve-root \
    -o go-template='{{ .data.ZO_ROOT_USER_EMAIL | base64decode }}:{{ .data.ZO_ROOT_USER_PASSWORD | base64decode }}' \
    | base64 | tr -d '\n'

OpenObserve UI: kubectl -n observability port-forward svc/openobserve 5080
EOF
}

STEPS=(apis cluster registry network buckets identity github addons observability data summary)

main() {
  local steps=("$@")
  [ "${#steps[@]}" -gt 0 ] || steps=("${STEPS[@]}")
  for step in "${steps[@]}"; do
    case " ${STEPS[*]} " in
      *" $step "*) "step_$step" ;;
      *) echo "unknown step '$step'; steps: ${STEPS[*]}" >&2; exit 64 ;;
    esac
  done
}

main "$@"
