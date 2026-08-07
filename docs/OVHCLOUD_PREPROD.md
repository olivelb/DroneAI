# OVHcloud realistic preproduction runbook

This is the source of truth for the first DroneAI cloud deployment. It is a
change-friendly preproduction environment, not a production platform. No
Terraform apply, DNS edit or quota increase is performed by repository tests or
CI.

## Fixed decisions

| Area | Preproduction choice | Boundary |
|---|---|---|
| Project | `fe7dc1254a9847849e0d29b01fe39b22` | The identifier is not a credential. |
| Region | `GRA11` | Existing quota: 34 vCPU, 44 GB RAM, 8 instances, 1+ gateway and load balancer capacity. |
| Kubernetes | OVHcloud MKS Free | Managed control plane; upgrade to Standard only for a production SLO/multi-zone requirement. |
| CPU | One `b3-8`, autoscaling to two | Keeps the initial footprint below the current quota. |
| GPU | Pool disabled, zero initial nodes | Select a real GRA11 flavor and request quota before enabling it. |
| COLMAP memory | 16 GiB request, 32 GiB limit | Replaces the historical 80 GiB request; local qualification succeeded on a 32 GB host with 8 GB VRAM. |
| Kafka | One persistent in-cluster broker, 20 GiB | Realistic enough for integration, not highly available and not production-grade. |
| Database | Managed PostgreSQL with PostGIS | Created deliberately in Manager after the gateway egress IP is known. |
| Objects | Encrypted, versioned OVH S3 bucket | Terraform prevents accidental deletion. |
| Images | OVH Harbor/MPR, Git SHA tags | `latest` is forbidden for preproduction service images. |
| DNS | `droneai-preprod.olembo.fr` and `api-droneai-preprod.olembo.fr` | Do not alter apex, `www`, MX, SPF or DKIM records. |

With the default maximum of one GPU node, Kubernetes cannot allocate one
exclusive `nvidia.com/gpu` to COLMAP and another to IA at the same time. Scale
one worker to zero while the other runs. Set `gpu_max_nodes = 2` only after a
quota and cost review if concurrent processing is required.

## 1. Prepare credentials locally

Install Terraform 1.14.x, kubectl, Helm 3 and Docker. Create an OVH API token
limited to the Public Cloud project and export it in the shell; never paste
these four values into chat, Git, a tfvars file or a Kubernetes manifest:

```bash
export OVH_ENDPOINT=ovh-eu
export OVH_APPLICATION_KEY='<local-secret>'
export OVH_APPLICATION_SECRET='<local-secret>'
export OVH_CONSUMER_KEY='<local-secret>'
```

For a first deployment, restrict the token to `GET`, `POST`, `PUT` and `DELETE`
under `/cloud/project/fe7dc1254a9847849e0d29b01fe39b22/*`. Tighten it later
once the exact resource operations have been observed.

Terraform state includes a Kubernetes client private key. Store the working
copy on an encrypted disk with user-only permissions and back it up securely.

## 2. Validate and review the Terraform plan

From the repository root:

```bash
cd infra/ovh/preprod
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform fmt -check
terraform validate
terraform plan -out=preprod.tfplan
terraform show preprod.tfplan
```

This plan must show no GPU pool. Confirm every price-bearing resource in the
OVHcloud calculator/Manager before continuing: one `b3-8`, gateway `s`, MPR
SMALL, one Object Storage bucket and the load balancer later created by the
ingress controller.

Only after review and explicit approval:

```bash
terraform apply preprod.tfplan
umask 077
terraform output -raw kubeconfig > kubeconfig-preprod.yaml
export KUBECONFIG="$PWD/kubeconfig-preprod.yaml"
kubectl get nodes
```

The repository and CI never execute these commands automatically.

## 3. Finish the managed services

### Harbor registry

In OVH Manager, open the new Managed Private Registry, generate identification
details, open the Harbor UI and create a private project named `droneai`.
Create separate credentials for image publishing and a read-only robot account
for Kubernetes.

```bash
REGISTRY_HOST="$(terraform output -raw registry_host)"
docker login "$REGISTRY_HOST"
kubectl create namespace drone-ai-preprod --dry-run=client -o yaml | kubectl apply -f -
kubectl -n drone-ai-preprod create secret docker-registry drone-ai-registry \
  --docker-server="$REGISTRY_HOST" \
  --docker-username='<pull-robot-name>' \
  --docker-password='<pull-robot-secret>'
```

### Object Storage

In `Public Cloud > Object Storage > S3 users`, create a dedicated DroneAI S3
user and grant it only read/write access to the bucket returned by
`terraform output -raw object_storage_bucket`. Record the access and secret
keys in a password manager. Do not put them in Terraform variables.

### PostgreSQL/PostGIS

In `Public Cloud > Databases`, create PostgreSQL 17 in `GRA` with the smallest
Essential flavor that meets the current offer, deletion protection enabled and
at least 20 GB storage. Create database `droneai` and application user
`droneai_app`. Allow only the `/32` public egress IP reported by
`terraform output -json gateway_public_ips`.

Connect using the TLS URI supplied by OVHcloud and initialize PostGIS:

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
SELECT PostGIS_Full_Version();
```

Keep the final percent-encoded application URI in the password manager. The
Alembic migration Job will create/upgrade DroneAI tables during the Helm
installation.

## 4. Install cluster add-ons

Install pinned releases, then wait for readiness:

```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo add jetstack https://charts.jetstack.io
helm repo update

helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  --version 4.15.1 \
  --namespace ingress-nginx --create-namespace \
  --set controller.service.type=LoadBalancer \
  --wait --timeout 10m

helm upgrade --install cert-manager jetstack/cert-manager \
  --version v1.21.0 \
  --namespace cert-manager --create-namespace \
  --set crds.enabled=true \
  --wait --timeout 10m

cp infra/kubernetes/ovh-preprod/cluster-issuer.yaml.example /tmp/cluster-issuer.yaml
# Replace REPLACE_ACME_EMAIL with a monitored mailbox before applying.
kubectl apply -f /tmp/cluster-issuer.yaml
kubectl -n ingress-nginx get service ingress-nginx-controller
```

Record the ingress service external IP. In the OVH DNS zone for `olembo.fr`,
add two `A` records with TTL 300 during preproduction:

- `droneai-preprod` to that external IP;
- `api-droneai-preprod` to that external IP.

Do not change the current apex/`www` welcome records or any mail records. Check
propagation before requesting certificates:

```bash
dig +short droneai-preprod.olembo.fr A
dig +short api-droneai-preprod.olembo.fr A
```

## 5. Publish immutable images

Use the exact commit that will be deployed. This is the only step that can
perform the long CUDA/COLMAP base build, and only when the local base is absent
or `REBUILD_COLMAP_BASE=1` is explicitly set:

```bash
GIT_SHA="$(git rev-parse HEAD)"
REGISTRY_PROJECT="<registry-host>/droneai"
scripts/deploy/publish-preprod-images.sh "$REGISTRY_PROJECT" "$GIT_SHA"
```

CI does not build COLMAP/CUDA for Terraform, documentation or Helm-only changes.

## 6. Create Kubernetes Secrets

Create them directly from your password manager or an external-secrets system.
For the first test, the required keys and names are:

```bash
kubectl -n drone-ai-preprod create secret generic drone-ai-storage-preprod \
  --from-literal=s3-access-key='<S3_ACCESS_KEY>' \
  --from-literal=s3-secret-key='<S3_SECRET_KEY>' \
  --from-literal=database-url='<TLS_POSTGRESQL_URI>'

kubectl -n drone-ai-preprod create secret generic drone-ai-api-auth \
  --from-literal=api-keys.json='[{"key":"<RANDOM_32_PLUS_CHAR_KEY>","subject":"preprod-admin","role":"admin"}]' \
  --from-literal=session-secret='<RANDOM_32_PLUS_CHAR_SESSION_SECRET>'
```

Do not reuse examples or local-development passwords. Create `hf-token` only
before enabling the IA worker:

```bash
kubectl -n drone-ai-preprod create secret generic hf-token \
  --from-literal=HF_TOKEN='<HUGGINGFACE_TOKEN>'
```

## 7. Deploy the CPU control plane first

Copy the tracked overlay to the ignored local file and replace the three
placeholders: registry URL, S3 endpoint and Git SHA. Do not add credentials.

```bash
cp charts/drone-ai/values-ovh-preprod.example.yaml \
  charts/drone-ai/values-ovh-preprod.local.yaml
$EDITOR charts/drone-ai/values-ovh-preprod.local.yaml

helm lint charts/drone-ai
helm template drone-ai charts/drone-ai \
  -f charts/drone-ai/values-ovh-preprod.local.yaml > /tmp/drone-ai-preprod.yaml

helm upgrade --install drone-ai charts/drone-ai \
  --namespace drone-ai-preprod --create-namespace \
  -f charts/drone-ai/values-ovh-preprod.local.yaml \
  --wait --wait-for-jobs --timeout 15m

kubectl -n drone-ai-preprod get pods,pvc,ingress
kubectl -n drone-ai-preprod rollout status deployment/dashboard-api --timeout=5m
kubectl -n drone-ai-preprod rollout status deployment/dashboard-frontend --timeout=5m
curl --fail https://api-droneai-preprod.olembo.fr/
```

At this stage Kafka, processing, API and frontend run; GPU workers are absent.

## 8. Enable and qualify the GPU pool

First select an available GRA11 GPU flavor in Manager. Add its vCPU and RAM to
the CPU pool maximum and request only the missing quota. Then edit the untracked
`terraform.tfvars`:

```hcl
enable_gpu_pool = true
gpu_flavor      = "<confirmed-GRA11-flavor>"
gpu_max_nodes   = 1
```

Run a new `terraform plan`, review price/quota, and apply it explicitly. Confirm
that MKS exposes the GPU before changing Helm values:

```bash
kubectl get nodes -L droneai.io/pool,droneai.io/gpu
kubectl get nodes -o custom-columns=NAME:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu
```

Set both `colmapWorker.enabled` and `iaWorker.enabled` to `true` in the local
values file and upgrade Helm. With one GPU node, immediately keep just one
worker active:

```bash
helm upgrade drone-ai charts/drone-ai \
  --namespace drone-ai-preprod \
  -f charts/drone-ai/values-ovh-preprod.local.yaml \
  --wait --wait-for-jobs --timeout 15m

# COLMAP phase
kubectl -n drone-ai-preprod scale deployment/ia-worker --replicas=0
kubectl -n drone-ai-preprod scale deployment/colmap-worker --replicas=1

# IA phase: reverse the replicas after COLMAP has completed.
kubectl -n drone-ai-preprod scale deployment/colmap-worker --replicas=0
kubectl -n drone-ai-preprod scale deployment/ia-worker --replicas=1
```

Because this is a new cloud GPU architecture, run one `nvidia-smi` pod smoke
test and one small end-to-end mission. Do not rerun the long COLMAP/CUDA build
suite unless the cloud GPU architecture, CUDA/COLMAP versions or CTests differ
from the already qualified build.

## 9. Acceptance and rollback

Acceptance requires healthy TLS, successful authentication, a completed
Alembic migration, S3 upload/readback, Kafka persistence after broker restart,
one small COLMAP mission and one IA phase. Record the image Git SHA and
`helm history drone-ai -n drone-ai-preprod`.

Application rollback:

```bash
helm history drone-ai -n drone-ai-preprod
helm rollback drone-ai <REVISION> -n drone-ai-preprod --wait --timeout 15m
```

For infrastructure teardown, first retain/export all useful objects. The S3
bucket intentionally blocks `terraform destroy`. To keep the bucket while
destroying the platform, remove only its state binding, then review a destroy
plan before applying it:

```bash
terraform state rm ovh_cloud_project_storage.assets
terraform plan -destroy -out=destroy.tfplan
terraform show destroy.tfplan
# terraform apply destroy.tfplan  # only after explicit approval
```

Deleting the retained bucket is a separate, deliberate OVHcloud operation.
