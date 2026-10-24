# GCP bootstrap

One-time setup for running the AgentIDE backend on GKE. [`bootstrap.sh`](bootstrap.sh)
creates the cloud resources and installs the cluster add-ons and data services.
After that, app deploys use only the [`agentide`](../helm/agentide) chart.

## What it creates

| Step | Resources |
|---|---|
| `apis` | Enables Container, Artifact Registry, Compute, Storage, IAM, IAM Credentials, STS and Resource Manager APIs |
| `cluster` | GKE Autopilot cluster `agentide` (Regular channel) and namespaces `agentide-dev`, `agentide-prod` and `observability` |
| `registry` | Docker repository `agentide` in Artifact Registry. A cleanup policy keeps `v*` releases and the 10 newest versions of each image, then deletes `sha-*` builds after 30 days |
| `network` | Global static IPs `agentide-dev-ip` and `agentide-prod-ip` |
| `buckets` | `<project>-agentide-uploads-{dev,prod}` and `<project>-agentide-pg-backups-{dev,prod}` |
| `identity` | Workload Identity grants: KSA `agentide` gets `storage.objectUser` on its env's uploads bucket. KSA `agentide-pg` gets `storage.objectAdmin` and `storage.legacyBucketReader` on its env's backup bucket |
| `github` | WIF pool `github` and provider `agentide`, which accept only `mohitpg/AgentIDE`. Service account `gh-deployer` gets `artifactregistry.writer` on the repository and `container.developer` on the project |
| `addons` | cert-manager, the CloudNativePG operator and the Barman Cloud plugin (in `cnpg-system`) |
| `observability` | OpenObserve (`openobserve-standalone` chart) in `observability`, with its root user taken from the secret `openobserve-root` |
| `data` | The secret `agentide-rabbitmq` and the [`agentide-data`](../helm/agentide-data) chart in each env namespace: Postgres + pgvector, Redis and RabbitMQ |
| `summary` | Prints the GitHub variables and secrets to set |

Each step is safe to re-run. It creates only what's missing and never regenerates
an existing password.

## Run it

Use [Cloud Shell](https://shell.cloud.google.com), which already has gcloud,
kubectl, helm and openssl. On your own machine you need the same tools plus
`gke-gcloud-auth-plugin` (`gcloud components install gke-gcloud-auth-plugin`).
Your account needs Owner on the project, or equivalent IAM admin rights.

```bash
git clone https://github.com/mohitpg/AgentIDE && cd AgentIDE
PROJECT_ID=<your-project> bash infra/gcp/bootstrap.sh
```

To re-run a single step, name it, for example
`PROJECT_ID=<your-project> bash infra/gcp/bootstrap.sh data summary`. Optional
settings (region, chart version pins, OpenObserve root email and password) are
listed at the top of the script.

## Then, in GitHub

1. Create the environments `dev` and `production`. Add required reviewers to
   `production` if you want an approval gate.
2. Set the variables `GCP_PROJECT`, `GCP_REGION`, `WIF_PROVIDER` and `DEPLOYER_SA`
   at repository level. Set `API_HOST` per environment. The `summary` step prints
   every value. Optionally, set `AUTH_ADMIN_EMAILS` per environment (do it for
   `production`, or the first person to sign in becomes admin), and
   `GKE_CLUSTER` if the cluster isn't named `agentide`.
3. In each environment, add the secrets `SECRET_KEY`, `A2A_AUTH_TOKEN`,
   `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `SERPER_API_KEY`,
   `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `OPENOBSERVE_AUTH_TOKEN`. Use
   different `SECRET_KEY` and `A2A_AUTH_TOKEN` values for dev and prod
   (`openssl rand -hex 32`).

`RABBITMQ_PASSWORD` is no longer a GitHub secret. It's generated into the
cluster secret `agentide-rabbitmq`, and both charts read it from there.

## Deploying by hand (before the workflows exist)

Build and push the seven images. Each one is named after its package without
the `agentide-` prefix:

```bash
gcloud auth configure-docker us-central1-docker.pkg.dev
REG=us-central1-docker.pkg.dev/$PROJECT_ID/agentide
TAG=sha-$(git rev-parse --short HEAD)
for img in api workers agent-summarizer agent-code agent-rag agent-vision agent-web; do
  docker build -f infra/docker/python.Dockerfile --build-arg PACKAGE=agentide-$img -t $REG/$img:$TAG . &&
  docker push $REG/$img:$TAG
done
```

Create the app secret from an env file that is never committed (one `KEY=value`
per line, with the keys listed above):

```bash
kubectl -n agentide-dev create secret generic agentide-secrets --from-env-file=secrets.dev.env
```

Then install the chart:

```bash
helm upgrade --install agentide infra/helm/agentide -n agentide-dev \
  -f infra/helm/agentide/values-dev.yaml \
  --set gcp.project=$PROJECT_ID --set gcp.region=us-central1 \
  --set ingress.host=dev-api.<ip-dashed>.sslip.io --set image.tag=$TAG \
  --rollback-on-failure --wait --timeout 15m
```

The first deploy's certificate takes 15–60 minutes. Check it with
`kubectl -n agentide-dev describe managedcertificate api`. Until it's `Active`,
HTTPS fails and HTTP redirects to it.

## Day to day

Port-forwards for the tools without a public endpoint:

```bash
kubectl -n observability port-forward svc/openobserve 5080       # traces, logs, metrics: orgs dev and prod
kubectl -n agentide-dev port-forward svc/rabbitmq 15672           # RabbitMQ management (user agentide)
kubectl -n agentide-dev exec -it agentide-pg-1 -- psql agentide   # psql as the postgres superuser
```

Backups:

```bash
kubectl -n agentide-prod get scheduledbackups,backups
```

The [cnpg kubectl plugin](https://cloudnative-pg.io/documentation/current/kubectl-plugin/)
command `kubectl cnpg status agentide-pg -n agentide-prod` shows the state of WAL
archiving and the last backup.

To restore, create a new Cluster that bootstraps from the `ObjectStore`. See the
Barman Cloud plugin's recovery docs. Restoring in place isn't supported.

The data chart is upgraded only by hand, with the `data` step or the
`helm upgrade` command at the top of its `values.yaml`. Its StatefulSets' volume
sizes are fixed when they're created. To grow one, edit its PVC.

## Tearing down

Delete the namespaces first. That releases the load balancers and the volumes'
disks, which deleting the cluster alone can leave behind:

```bash
kubectl delete namespace agentide-dev agentide-prod observability
gcloud container clusters delete agentide --region us-central1
gcloud compute addresses delete agentide-dev-ip agentide-prod-ip --global
gcloud storage rm -r gs://$PROJECT_ID-agentide-uploads-dev gs://$PROJECT_ID-agentide-uploads-prod \
  gs://$PROJECT_ID-agentide-pg-backups-dev gs://$PROJECT_ID-agentide-pg-backups-prod
gcloud artifacts repositories delete agentide --location us-central1
gcloud compute disks list   # check nothing is left
```
