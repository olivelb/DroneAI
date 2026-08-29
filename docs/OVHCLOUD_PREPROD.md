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
| GPU | Hybrid: local RTX 4070 worker; OVH `l4-90` pool retained at zero nodes | Keeps the live platform inside the 44 GB RAM quota and incurs no OVH GPU compute charge. |
| COLMAP memory | 16 GiB request, 32 GiB limit | Replaces the historical 80 GiB request; local qualification succeeded on a 32 GB host with 8 GB VRAM. |
| Kafka | One persistent in-cluster broker, 20 GiB | Realistic enough for integration, not highly available and not production-grade. |
| Database | In-cluster PostgreSQL 16 + PostGIS 3.5, 20 GiB | Low-cost first deployment; single-node and explicitly not production-grade. |
| Objects | Encrypted, versioned OVH S3 bucket | Terraform prevents accidental deletion. |
| Backups | Separate encrypted OVH S3 bucket, seven daily PostgreSQL slots | Dedicated read/write credentials cannot delete objects; Terraform prevents bucket deletion. |
| Terraform state | Separate encrypted, versioned OVH S3 bucket | Dedicated S3 user, least-privilege object policy and native `.tflock` locking. |
| Images | OVH Harbor/MPR, OCI digests | Git tags identify builds; deployed application references must use SHA-256 digests. |
| DNS | `droneai-preprod.olembo.fr` and `api-droneai-preprod.olembo.fr` | Do not alter apex, `www`, MX, SPF or DKIM records. |

With the default maximum of one GPU node, keep global and per-mission GPU
resource concurrency at one so reconstruction/Gaussian/raster/detection Jobs
run sequentially. Set `gpu_max_nodes = 2` only after a quota and cost review if
cross-mission concurrency is required.

The five blocking one-shot executors are qualified on BIGZEN K3s/RTX 3090;
the optional non-blocking `gaussian_viewer` adapter is implemented but is not
part of that scientific-chain qualification; see the
[Chapelle Q3 addendum](benchmarks/chapelle-banyuls-p4-fast-e2e-2026-08-09.md#q3-kubernetes-five-job-qualification-addendum).
That result replaces the former executor-availability blocker, but it does not
create OVH GPU quota or authorize waking the zero-node pool. The next OVH GPU
qualification must deploy `stageJobs.enabled=true` with the reviewed six-entry OCI-digest
executor map and one-per-mission GPU concurrency. The dated hybrid worker run
later in this document remains historical cloud evidence.

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

Terraform state includes a Kubernetes client private key and, during backend
bootstrap, the dedicated S3 secret. Store the local bootstrap copy on an
encrypted disk with user-only permissions. Never commit a state, plan,
`.tfbackend` file or backend credential file.

## 2. Bootstrap and secure Terraform state

Remote state was established on 7 August 2026 in two deliberately separate
operations. The first operation created the backend resources while the
existing state remained local:

- bucket `droneai-preprod-tfstate-fe7dc125`, AES-256 server-side encrypted,
  versioned and protected by `prevent_destroy`;
- a dedicated OVHcloud S3 user and credentials;
- an S3 user policy limited to this bucket, with `GetObject`/`PutObject` on
  `preprod/terraform.tfstate` and `GetObject`/`PutObject`/`DeleteObject` only on
  `preprod/terraform.tfstate.tflock`.

The state object itself has no `DeleteObject` permission. Bucket versioning is
the recovery mechanism for an accidental overwrite. Object Lock is not enabled
because Terraform must be able to delete its short-lived lock object.

The one-time bootstrap was validated with the backend disabled:

```bash
cd infra/ovh/preprod
test -f terraform.tfvars || cp terraform.tfvars.example terraform.tfvars
terraform init -backend=false
terraform fmt -check
terraform validate
terraform plan -out=state-bootstrap.tfplan
terraform show state-bootstrap.tfplan
sha256sum state-bootstrap.tfplan
```

The reviewed bootstrap plan showed exactly four additions and no change or
deletion: state bucket, S3 user, credential and user policy. Its SHA-256 was
`2bffda59836bacf2a5a4d6f8e77cb0240d80c707a308f5c5f622fd5368a25b7d`.
The bucket has no fixed monthly resource fee; only stored bytes and S3
operations are billed. The user, credential and policy are not compute
resources.

Only after review and explicit approval:

```bash
terraform apply state-bootstrap.tfplan
cd ../../..
TERRAFORM_BIN=/home/olivier/.cache/codex/terraform-1.14.6/terraform \
  scripts/deploy/export-terraform-backend-env.sh
chmod 0600 ~/.config/droneai/terraform-backend.env
cp infra/ovh/preprod/backend-preprod.s3.tfbackend.example \
  infra/ovh/preprod/backend-preprod.s3.tfbackend
```

The export helper does not print either key. The ignored environment file is
the only place from which `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` should
be loaded. The otherwise empty S3 backend block is tracked in `backend.tf`; it
was added only after the one-time bootstrap apply:

```hcl
terraform {
  backend "s3" {}
}
```

Then migrate and verify the state:

```bash
cd infra/ovh/preprod
set -a
source ~/.config/droneai/terraform-backend.env
set +a
terraform init -migrate-state -backend-config=backend-preprod.s3.tfbackend
terraform state pull | sha256sum
terraform plan -detailed-exitcode
cd ../../..
TERRAFORM_BIN=/home/olivier/.cache/codex/terraform-1.14.6/terraform \
  scripts/deploy/test-terraform-backend-lock.sh
```

The migration, remote `state pull`, empty plan (exit code `0`) and concurrent
lock rejection all passed on 7 August 2026. The lock test starts two read-only
plans and succeeds only when S3 rejects the contender with `Error acquiring the
state lock`.

Terraform left a sensitive `0644` migration backup in the working tree. It was
copied and verified before removal. Two recovery copies are retained outside
the repository as `~/.config/droneai/terraform-preprod-bootstrap.tfstate.backup`
and `~/.config/droneai/terraform-preprod-migration.tfstate.backup`, both mode
`0600`. Keep them until remote-state recovery has been rehearsed; never remove
a local state merely because `terraform init` returned successfully.

HashiCorp supports S3 lock files from Terraform 1.10 onward. The backend example
sets `use_lockfile = true`, uses the OVHcloud regional S3 endpoint, and obtains
all credentials from environment variables rather than backend arguments. OVH
uses `GRA` in its Public Cloud API but requires lowercase `gra` in the S3
signature region.

For normal use from a fresh checkout after the completed migration:

```bash
set -a
source ~/.config/droneai/ovh.env
source ~/.config/droneai/terraform-backend.env
set +a
export GODEBUG=http2client=0
terraform -chdir=infra/ovh/preprod init \
  -backend-config=backend-preprod.s3.tfbackend.example
terraform -chdir=infra/ovh/preprod plan -detailed-exitcode
```

`GODEBUG=http2client=0` is a local WSL transport workaround: on the tested
machine, the OVH provider intermittently returned TLS `EOF`/`record overflow`
with HTTP/2 while the same API endpoint remained reachable with `curl`.

## 3. Review the platform and export kubeconfig

For a new environment, the initial platform plan must show no GPU pool. Confirm
every price-bearing resource in the OVHcloud calculator/Manager before applying:
one `b3-8`, gateway `s`, MPR SMALL, application Object Storage bucket and the
load balancer later created by the ingress controller.

After an explicitly approved platform apply:

```bash
cd infra/ovh/preprod
umask 077
terraform output -raw kubeconfig > kubeconfig-preprod.yaml
export KUBECONFIG="$PWD/kubeconfig-preprod.yaml"
kubectl get nodes
```

The repository and CI never execute these commands automatically.

## 4. Finish the managed services

### Harbor registry

Terraform creates a temporary `droneai-bootstrap` registry account. Read its
sensitive password locally, open the Harbor UI and create a private project
named `droneai`. Create separate credentials for image publishing and a
read-only robot account for Kubernetes. Do not use the bootstrap account in a
workload.

The idempotent bootstrap script creates the private project and a project-level
pull-only robot, verifies its registry access, and writes its password directly
to the `drone-ai-registry` Kubernetes Secret without printing it:

```bash
export KUBECONFIG="$HOME/.config/droneai/kubeconfig-preprod.yaml"
export TERRAFORM_BIN="$HOME/.cache/codex/terraform-1.14.6/terraform"
scripts/deploy/bootstrap-harbor-preprod.sh
```

```bash
REGISTRY_HOST="$(terraform output -raw registry_host)"
REGISTRY_LOGIN="$(terraform output -raw registry_bootstrap_login)"
REGISTRY_PASSWORD="$(terraform output -raw registry_bootstrap_password)"
printf '%s' "$REGISTRY_PASSWORD" | docker login "$REGISTRY_HOST" \
  --username "$REGISTRY_LOGIN" --password-stdin
kubectl create namespace drone-ai-preprod --dry-run=client -o yaml | kubectl apply -f -
kubectl -n drone-ai-preprod create secret docker-registry drone-ai-registry \
  --docker-server="$REGISTRY_HOST" \
  --docker-username='<pull-robot-name>' \
  --docker-password='<pull-robot-secret>'
```

The manual `kubectl create secret` command is only a recovery procedure when
rotating an existing robot; it is not needed after the bootstrap script.

### Object Storage

Terraform creates a dedicated application S3 user and grants it bucket
inspection plus object read/write/delete access only within the bucket returned
by `terraform output -raw object_storage_bucket`. Store the two sensitive
outputs in a local mode-0600 file or password manager; do not put them in
Terraform variables or Kubernetes values files.

Mission Studio sends dataset parts directly to this bucket. Configure its CORS
rule once after the bucket and scoped credentials exist; the origin must be the
public frontend origin and `ETag` must be exposed so multipart completion can
be verified:

```bash
set -a
source "$HOME/.config/droneai/ovh-preprod-s3.env"
set +a
export DRONEAI_UPLOAD_ALLOWED_ORIGINS="https://droneai.olembo.fr"
scripts/deploy/configure-s3-upload-cors.sh
```

The environment file supplies `S3_ENDPOINT`, `S3_BUCKET`, `S3_REGION`,
`S3_ACCESS_KEY` and `S3_SECRET_KEY` and stays outside the repository. The
script uses the S3 API only; it does not write credentials to Terraform state.

Qualify the scoped credentials with a temporary object that is automatically
deleted after the test:

```bash
export TERRAFORM_BIN="$HOME/.cache/codex/terraform-1.14.6/terraform"
scripts/deploy/test-ovh-s3-assets.sh
```

PostgreSQL backups use a separate bucket and identity. The backup policy is
limited to `GetObject` and `PutObject` below `postgres/*`; it deliberately has
no `DeleteObject` permission. The bucket is AES-256 encrypted, is not versioned
and is protected by `prevent_destroy`. Seven deterministic daily keys bound
storage growth while retaining one week:

```text
postgres/daily-1.dump
...
postgres/daily-7.dump
```

The `postgres-backup` CronJob runs at `02:17 Etc/UTC`, uploads a PostgreSQL
custom-format dump with SSE-OMK, downloads it again and compares byte count and
SHA-256 before succeeding. Its credentials are stored only in the
`drone-ai-backup-preprod` Secret. The Helm test independently downloads the
current slot and performs a real restore into an isolated, disposable
PostgreSQL/PostGIS instance inside the test pod; it never restores into the
active database.

### PostgreSQL/PostGIS

PostgreSQL is a cost gate and must not be provisioned implicitly. As verified
on 7 August 2026, the smallest managed PostgreSQL offer is Essential DB1-4 with
40 GB, at approximately `0.0814 EUR excl. tax/hour` or `59.42 EUR excl.
tax/month`. If selected, create PostgreSQL 17 in `GRA` with deletion protection,
database `droneai` and application user `droneai_app`. Allow only the `/32`
public egress IP reported by `terraform output -json gateway_public_ips`.

For the first change-heavy cloud test, the lower-cost alternative is the
in-chart PostGIS instance on a 20 GB `csi-cinder-high-speed-gen2` PVC. Together
with Kafka's 20 GB PVC, both volumes cost approximately `3.83 EUR excl.
tax/month` at `0.000131 EUR/GB/hour`. This alternative is single-node and must
be migrated to managed PostgreSQL before production or any availability/SLA
qualification. Current prices must be rechecked on the
[OVHcloud Public Cloud price page](https://www.ovhcloud.com/fr/public-cloud/prices/)
before ordering.

Connect using the TLS URI supplied by OVHcloud and initialize PostGIS:

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
SELECT PostGIS_Full_Version();
```

Keep the final percent-encoded application URI in the password manager. The
Alembic migration Job will create/upgrade DroneAI tables during the Helm
installation.

### Managed-service bootstrap result (7 August 2026)

Terraform applied four non-billable access resources with no update or
deletion: one Harbor bootstrap user, one S3 application user, its credential
and its bucket-only policy. The S3 qualification successfully wrote, read and
deleted a temporary object. Harbor contains the private `droneai` project and
a verified project-level pull-only robot whose secret is stored only in the
`drone-ai-registry` Kubernetes Secret.

The CPU-only publication produced these immutable artifacts for commit
`6b5a17d8261618980697514cf716214d45edac85`:

- `drone-dashboard-api`: `sha256:08a5a588cd85410ebe06673a67f2bcaddfa5e2ce56a9cfbff4adc399935f344b`;
- `drone-dashboard-frontend`: `sha256:86608159b360a2a8f7b0c4af0a7f572d35314b76b6c488569dda0c1bc21e9152`.

No IA, COLMAP or CUDA image was built or pushed. These dated CPU artifacts
are historical evidence, not images for the current cleanup. Rebuild all four
current application images; the former processing image is retired.

## 5. Install cluster add-ons

`ingress-nginx` reached end of life in March 2026 and is intentionally not used.
Install the pinned, maintained Traefik and cert-manager releases, then wait for
readiness. The Traefik values explicitly request an OVHcloud Octavia `small`
Load Balancer and do not expose the dashboard:

```bash
helm repo add traefik https://traefik.github.io/charts
helm repo update

helm upgrade --install traefik traefik/traefik \
  --version 41.1.1 \
  --namespace traefik --create-namespace \
  --values infra/kubernetes/ovh-preprod/traefik-values.yaml \
  --wait --timeout 10m

helm upgrade --install cert-manager \
  oci://quay.io/jetstack/charts/cert-manager \
  --version v1.21.1 \
  --namespace cert-manager --create-namespace \
  --values infra/kubernetes/ovh-preprod/cert-manager-values.yaml \
  --wait --timeout 10m

kubectl apply -f infra/kubernetes/ovh-preprod/cluster-issuer.yaml.example
kubectl -n traefik get service traefik
```

The selected chart versions are Traefik `41.1.1` / proxy `3.7.9` and
cert-manager `1.21.1`; both declare compatibility with Kubernetes 1.35. Record
the Traefik service external IP. In the OVH DNS zone for `olembo.fr`,
add two `A` records. Prefer TTL 300 for frequently changed preproduction
records; the initial deployment used the zone default TTL of 3600 seconds:

- `droneai-preprod` to that external IP;
- `api-droneai-preprod` to that external IP.

Do not change the current apex/`www` welcome records or any mail records. Check
propagation before requesting certificates:

```bash
dig +short droneai-preprod.olembo.fr A
dig +short api-droneai-preprod.olembo.fr A
```

### First live deployment snapshot (7 August 2026)

The first OVHcloud deployment produced the following verified state:

- Traefik Helm chart `41.1.1`, proxy `3.7.9`, one ready replica and no restart;
- OVHcloud Octavia `small` Load Balancer at `91.134.64.82` on ports 80/443;
- cert-manager `1.21.1` ready and `letsencrypt-prod` registered with
  `admin@olembo.fr`;
- authoritative and public DNS for both preproduction hostnames resolving to
  `91.134.64.82` with TTL 3600;
- HTTP returning `308 Permanent Redirect` to HTTPS for both hostnames;
- HTTPS returning Traefik `404` until the DroneAI chart creates its Ingress
  routes. This is expected and confirms that DNS, the Load Balancer and
  Traefik are reachable before the application deployment.

No apex, `www`, MX, SPF, DKIM or other mail record was changed.

## 6. Publish immutable images

Use a clean checkout of the exact full 40-character HEAD commit that will be deployed. The default command publishes only
the two CPU control-plane images and never builds or publishes CUDA/GPU
images:

```bash
GIT_SHA="$(git rev-parse HEAD)"
REGISTRY_PROJECT="<registry-host>/droneai"
scripts/deploy/publish-preprod-images.sh "$REGISTRY_PROJECT" "$GIT_SHA"
```

On the configured OVHcloud workstation, the wrapper retrieves the Harbor
bootstrap account from protected Terraform state, forces CPU-only publication
and logs out even when a build fails:

```bash
export TERRAFORM_BIN="$HOME/.cache/codex/terraform-1.14.6/terraform"
scripts/deploy/publish-ovh-preprod-cpu.sh
```

GPU publication requires `INCLUDE_GPU_IMAGES=1`. It reuses the local
`drone-colmap-base:GIT_SHA` for the selected revision, passes that exact reference
to the application build and fails if that base is absent or its OCI revision
label does not match. The label is a consistency check, not signed provenance. The long base build
is possible only when both `INCLUDE_GPU_IMAGES=1` and
`REBUILD_COLMAP_BASE=1` are explicitly set. A normal PR, merge, documentation,
Terraform, Helm or CPU application change must never set those flags.

CI does not build COLMAP/CUDA for Terraform, documentation or Helm-only changes.

## 7. Create Kubernetes Secrets

Create them directly from your password manager or an external-secrets system.
For the first test, the required keys and names are:

```bash
kubectl -n drone-ai-preprod create secret generic drone-ai-storage-preprod \
  --from-literal=s3-access-key='<S3_ACCESS_KEY>' \
  --from-literal=s3-secret-key='<S3_SECRET_KEY>' \
  --from-literal=database-url='<TLS_POSTGRESQL_OPERATOR_URI>' \
  --from-literal=api-database-url='<TLS_POSTGRESQL_API_RLS_URI>'

kubectl -n drone-ai-preprod create secret generic drone-ai-api-auth \
  --from-literal=api-keys.json='[{"key":"<RANDOM_32_PLUS_CHAR_KEY>","subject":"preprod-admin","role":"admin","organization_id":"ovh-preprod"}]' \
  --from-literal=session-secret='<RANDOM_32_PLUS_CHAR_SESSION_SECRET>' \
  --from-literal=credential-pepper='<DISTINCT_RANDOM_32_PLUS_CHAR_PEPPER>'
```

`api-database-url` must authenticate as a non-owner role with `NOSUPERUSER`
and `NOBYPASSRLS`. `database-url` remains the migration/worker connection.
Provision the role and grants before this Secret by following the
[PostgreSQL tenant RLS contract](contracts/postgres-tenant-rls-v1.md). The
staging Helm render rejects a shared key and API readiness rejects a role for
which RLS is inactive.

After migration `0025`, bootstrap `ovh-preprod`, issue and verify at least one
durable admin credential, then remove `api-keys.json`. Keep `session-secret` and
`credential-pepper`; rotating the pepper intentionally invalidates every
durable credential.

The protected preproduction overlay always enables bounded stage Jobs and
rejects fused compute. Before installing it, provision the five existing
Secrets named by `stageJobs.credentialSecrets` in the preproduction values.
Each must contain `stage-database-url`, `s3-access-key` and `s3-secret-key`,
using a distinct non-owner `NOBYPASSRLS` database role and least-privilege
object-storage principal. Helm rejects a
missing entry or a reused Secret name; the activation review must additionally
verify that the underlying principals are not copies of the same credentials.
See the [scoped credential gate](OPERATIONS.md#scoped-credential-gate-for-stage-jobs).

Do not reuse examples or local-development passwords. Create `hf-token` only
before enabling a SAM3 detection Job (or the compatibility IA worker):

```bash
kubectl -n drone-ai-preprod create secret generic hf-token \
  --from-literal=HF_TOKEN='<HUGGINGFACE_TOKEN>'
```

The Helm defaults pin `facebook/sam3` to a full Hugging Face commit in
`stageJobs.sam3.revision`. Keep that revision immutable in environment overlays;
upgrading it is a reviewed model change and creates a different provenance
manifest in analysis results.

BIGZEN qualification at commit `74b6d7a...` proved the cumulative 8/12/24 GB
selectors, Manifest v2, selective restore and a 4,160-tile Indexed SAM3 Job with
five receipts and a finalizer. This is implementation evidence, not an OVH
waiver. Before enabling the same flags on OVH, label the actual GPU node pool,
deploy the exact promoted image digests and repeat a target canary. No
CUDA/COLMAP rebuild is required unless their versions or the target GPU
architecture changed. See the
[BIGZEN qualification record](benchmarks/bigzen-stage-jobs-fanout-2026-08-10.md).

The same run sampled about 6.3 GiB VRAM at SAM3 batch one, while the pinned
processor resized every tile to 1,008 × 1,008. Commit `745e681...` encoded a
12 GiB minimum, batch one and a 1,024 px source-tile cap, then passed a focused
81-tile BIGZEN rerun on the exact immutable model revision. For a detection-only
pool, an OVH GPU with at least 12 GiB can therefore be canaried against this
contract. The complete five-Job pipeline still requires a 24 GiB-capable pool
for Gaussian training and filtering; this SAM3 change does not reduce those
independent envelopes. Repeat the focused canary on the chosen OVH SKU and
architecture before activation.

For the in-cluster PostgreSQL preproduction option, create or reconcile the
external Secrets without printing their values:

```bash
export TERRAFORM_BIN="$HOME/.cache/codex/terraform-1.14.6/terraform"
export KUBECONFIG="$HOME/.config/droneai/kubeconfig-preprod.yaml"
scripts/deploy/bootstrap-ovh-preprod-secrets.sh
```

## 8. Deploy the CPU control plane first

Copy the tracked overlay to the ignored local file and replace every
placeholder: registry URL, S3 endpoint, application/executor OCI digests and
target GPU architecture and `dashboardApi.proxy.trustedCidrs`. Determine the
direct source addresses from the live Traefik/LB path; never use `*`,
`0.0.0.0/0` or `::/0`. Do not add credentials. Retain the publisher's
RepoDigests and use them instead of Git tags; old tag-based protected overlays
must be migrated before their next Helm rollout.

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

The configured OVHcloud workstation can perform the same CPU-only deployment
with the qualified local executor values file. The wrapper resolves only non-secret
Terraform outputs, reconciles Kubernetes Secrets separately and uses Helm
`--atomic --wait --wait-for-jobs`:

```bash
export TERRAFORM_BIN="$HOME/.cache/codex/terraform-1.14.6/terraform"
export KUBECONFIG="$HOME/.config/droneai/kubeconfig-preprod.yaml"
scripts/deploy/deploy-ovh-preprod-cpu.sh
```

For an existing release, the wrapper reuses the deployed API and frontend
digests and requires the control worker to match the API. Supply both
`API_IMAGE` and `FRONTEND_IMAGE` explicitly for a new release, together
with `RELEASE_VALUES_FILE` for the qualified executor overlay as described
below. `IMAGE_TAG` is rejected; old tag-based releases require explicit
migration to digests.

At this stage Kafka, PostgreSQL, API, control workers and frontend run; GPU
Stage Jobs remain pending until a qualified GPU pool is available.

### First CPU control-plane deployment result (7 August 2026)

Helm release `drone-ai` revision 3 was deployed successfully in namespace
`drone-ai-preprod`. PostgreSQL, Kafka, processing, dashboard API and frontend
were all Ready with zero restart; the Alembic Job completed migrations `0001`
through `0005`. PostGIS reported version 3.5. The PostgreSQL 20 GiB PVC uses
`csi-cinder-high-speed-gen2`; the Kafka 20 GiB PVC uses
`csi-cinder-high-speed`.

The Apache Kafka container runs as UID/GID 1000 with `fsGroup: 1000`. Its data
mount uses the `kafka` subdirectory so the broker does not treat the ext4
`lost+found` directory as a Kafka log. A post-install/post-upgrade Helm hook
idempotently creates all seven application topics before acceptance checks.

The certificate `drone-ai-preprod-tls` is Ready for both hostnames and expires
on 5 November 2026. Both HTTPS endpoints returned `200`; plain HTTP redirected
to HTTPS. Authentication created and read back an HTTPS session for
`preprod-admin` with the `admin` role. A temporary object written from the API
pod was read back byte-for-byte and deleted from OVH S3.

Kafka was qualified beyond a TCP probe: both API and processing pods listed all
application topics; a temporary message was written, the broker Deployment was
restarted, and the message was read back from the same PVC before its temporary
topic was deleted. All application pods returned Ready with zero restart after
the test. At steady state before this controlled restart, the only node used
approximately 138 millicores and 2 GiB of memory (34% of allocatable memory),
leaving enough headroom for this CPU preproduction control plane. No GPU,
CUDA, IA or COLMAP build/test was run during this deployment.

### Backup and zero-node GPU preparation result (7 August 2026)

Terraform added the encrypted backup bucket, its dedicated user, credential
and prefix-scoped policy, plus an autoscaled `l4-90` GPU pool configured with
minimum/desired `0` and maximum `1`. The final authenticated Terraform plan
returned exit code `0` with **No changes**. Kubernetes reports one ready
`b3-8` CPU node and no GPU node, so the GPU pool currently has no hourly GPU
compute charge.

Helm release `drone-ai` revision 13 is deployed and its test suite is
`Succeeded`. The first manual backup uploaded and read back
`postgres/daily-5.dump` (48,076 bytes). The independent restore test then
restored the archive into a disposable local PostGIS instance and found 47
user tables. The certificate remained Ready, and both public HTTPS endpoints
returned `200` with successful certificate verification. No CUDA, COLMAP or GPU
build/test was run.

## 9. Hybrid GPU qualification inside the 44 GB quota

The first full cloud mission was qualified on 7 August 2026 without requesting
a quota increase. Kubernetes kept the single OVH `b3-8` CPU node for the API,
Kafka, PostgreSQL and processing worker. COLMAP/DroneGS and IA ran temporarily
on the development laptop's RTX 4070 Laptop GPU through authenticated local
workers connected to the OVH Kafka and S3 services. The OVH `l4-90` pool
remained at minimum/desired `0`, so this test allocated no cloud GPU and added
no OVH GPU compute charge.

The immutable cloud images were:

- API: `d7a4fa64ebb00605313bc0de816b6ddcf3c0f5f2`;
- processing: `2fd13828cba99842f2fdb239f11b160dc861c427`.

Helm release `drone-ai` revision 18 completed successfully. Mission
`ovh-gajan-e2e-20260807` used dataset `gajan-hybrid-e2e-20260807`, containing
25 contiguous 12 MP photographs (`DJI_0573.JPG` through `DJI_0597.JPG`). The
bounded profile used sequential OpenCV matching, a 2,400 px feature size,
5,000 DroneGS iterations, a 0.25 m orthomosaic and YOLO26l at confidence 0.20.

The real end-to-end result was:

- COLMAP registered 25/25 photographs and reconstructed 7,825 sparse points;
- alignment error was about 1.047 m mean and 1.080 m median;
- DroneGS completed 5,000 iterations in the available 8 GB VRAM, producing
  29,068 Gaussians before filtering and 16,148 after filtering;
- the final orthomosaic was 438 x 376 pixels at 0.25 m/pixel, with a height map;
- 72 final mission objects (205,975,146 bytes) were retained in OVH S3 after
  durable recovery cleanup, including both COGs, previews and provenance;
- processing produced one tile, IA processed that tile and found zero objects
  at the requested confidence; a valid empty `detections.geojson` was stored;
- all Kafka consumer groups reached zero lag and the public mission summary
  ended at `overall_status: success`, with COLMAP, TILER and IA all successful.

This qualification exposed and fixed eight integration defects: the explicit
`sm_89` CUDA architecture now propagates to every GPU build stage; synchronous
Kafka commits use the current `confluent-kafka` API; a new COLMAP consumer
replays uncommitted work from `earliest`; a valid single-block COG no longer
requires overviews; S3 response checksum validation is compatible with OVH;
uppercase OVH cloud regions are normalized for S3 request signatures; height
COG NoData values produce strict JSON while legacy sidecars remain readable;
and successful mission state can no longer regress during durable cleanup.
The source dataset was never modified. Long CUDA/COLMAP builds were not added
to ordinary PR or merge CI. The final non-CUDA suite passed 419 tests with 13
CuPy-only tests skipped, and Ruff passed across the repository.

For another temporary hybrid run, expose only the required broker endpoint
through a local `kubectl port-forward`, inject credentials through ignored
mode-0600 files/Kubernetes Secrets, and use a unique Kafka consumer group and
mission ID. Never commit a kubeconfig, S3 key, registry password or port-forward
endpoint. Stop the local workers and tunnel when terminal state and S3 outputs
have been verified.

### Optional: move the GPU worker into OVHcloud

The zero-node pool already uses the GRA11 `l4-90` flavor: 22 vCPU, 90 GB RAM
and one NVIDIA L4 with 24 GB VRAM. The live OVH catalog price checked on
7 August 2026 was EUR 0.75 excl. tax/hour when a node exists. This L4 is the
correct target for the existing `sm_89` portable CUDA build. Recheck the
[OVHcloud Public Cloud price page](https://www.ovhcloud.com/fr/public-cloud/prices/)
before scaling because catalog prices can change.

Current GRA11 quota is 34 vCPU and 44 GB RAM, with the `b3-8` CPU node using
2 vCPU and 8 GB. The first L4 therefore requires total RAM quota of at least
98 GB (an increase of 54 GB); current vCPU quota is sufficient for one CPU plus
one L4 node. There is no currently available OVH GPU flavor that fits beside
the CPU node inside 44 GB: even `t2-45` is a 45 GB instance before the existing
8 GB node is counted. Keep using the qualified hybrid design to stay at the
44 GB tier. Request and confirm the RAM quota increase only when a persistent
OVH-hosted GPU worker is actually required. The applied untracked
`terraform.tfvars` is equivalent to:

```hcl
enable_gpu_pool = true
gpu_flavor      = "l4-90"
gpu_max_nodes   = 1
```

After quota approval, scale the pool to one node through the reviewed
Terraform configuration or MKS autoscaling workflow. Follow OVHcloud's
[GPU workload guide](https://help.ovhcloud.com/csm/en-gb-public-cloud-kubernetes-deploy-gpu-application?id=kb_article_view&sysparm_article=KB0049707)
to install the NVIDIA runtime/operator required by the selected Kubernetes
version. Confirm that MKS exposes the GPU before changing Helm values:

```bash
kubectl get nodes -L droneai.io/pool,droneai.io/gpu
kubectl get nodes -o custom-columns=NAME:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu
```

Then assert the reviewed L4 24 GB capability cumulatively. Do not apply these
labels to an unverified flavor or to a heterogeneous node containing a smaller
GPU:

```bash
GPU_NODE="$(kubectl get nodes -l droneai.io/gpu=nvidia \
  -o jsonpath='{.items[0].metadata.name}')"
test -n "$GPU_NODE"
kubectl label node "$GPU_NODE" --overwrite \
  nvidia.com/gpu.present=true \
  droneai.io/gpu-vram-at-least-8gb=true \
  droneai.io/gpu-vram-at-least-12gb=true \
  droneai.io/gpu-vram-at-least-24gb=true
kubectl get node "$GPU_NODE" \
  -L nvidia.com/gpu.present,droneai.io/gpu-vram-at-least-8gb,droneai.io/gpu-vram-at-least-12gb,droneai.io/gpu-vram-at-least-24gb
```

For bounded Jobs, add to each immutable
executor entry only the toleration matching the actual GPU-pool taint, for
example:

```yaml
tolerations:
  - key: nvidia.com/gpu
    operator: Equal
    value: present
    effect: NoSchedule
```

The rendered Job adds the VRAM selector itself. An executor may add a stricter
pool/architecture selector but cannot weaken this resource-class requirement.

Use the Stage Job scheduler concurrency and resource-class limits to share a
single GPU. The retired Kafka worker Deployments cannot be enabled.


Because this is a new cloud GPU architecture, run one `nvidia-smi` pod smoke
test and one small end-to-end mission. Do not rerun the long COLMAP/CUDA build
suite unless the cloud GPU architecture, CUDA/COLMAP versions or CTests differ
from the already qualified build.

## 10. Deep sleep and recovery

Deep sleep retains the free MKS control plane, the private network, all three
protected S3 buckets and their scoped identities. It removes the CPU nodes,
Managed Private Registry, Gateway, public Load Balancer/Floating IP and the two
billable PVC-backed volumes. Never remove the Terraform state bucket or its S3
identity.

Before entering deep sleep, create a fresh PostgreSQL backup and run the real
restore test. The delete helper refuses to touch a DNS record unless exactly
one A record exists and it still targets the expected Load Balancer IP:

```bash
export KUBECONFIG="$HOME/.config/droneai/kubeconfig-preprod.yaml"
kubectl -n drone-ai-preprod create job --from=cronjob/postgres-backup \
  postgres-backup-deep-sleep-YYYYMMDD
kubectl -n drone-ai-preprod wait --for=condition=complete \
  job/postgres-backup-deep-sleep-YYYYMMDD --timeout=15m
helm test drone-ai --namespace drone-ai-preprod \
  --filter name=postgres-backup-restore-test --logs --timeout 15m

set -a
source "$HOME/.config/droneai/ovh.env"
set +a
scripts/deploy/delete-ovh-dns-a.sh --check olembo.fr 91.134.64.82 \
  droneai-preprod api-droneai-preprod
scripts/deploy/delete-ovh-dns-a.sh olembo.fr 91.134.64.82 \
  droneai-preprod api-droneai-preprod

kubectl -n drone-ai-preprod scale deployment --all --replicas=0
kubectl -n drone-ai-preprod wait --for=delete pod \
  -l app=postgres --timeout=5m
kubectl -n drone-ai-preprod delete pvc kafka-pvc postgres-pvc
kubectl -n traefik delete service traefik
```

Set `deep_sleep = true` only in the ignored local `terraform.tfvars`, create a
saved authenticated plan and require exactly zero additions, one in-place CPU
pool update and three deletions: the Registry, its bootstrap user and the
Gateway. No MKS, network, bucket or S3 identity may be deleted. Apply only that
reviewed plan and verify that Kubernetes reports zero nodes and OVHcloud no
longer reports the Registry, Gateway, Load Balancer, Floating IP or volumes.

To wake the platform, set `deep_sleep = false` and apply the reviewed reverse
Terraform plan. Recreate the Harbor project and pull secret, publish immutable
CPU images, run the normal Helm deployment wrapper, reinstall/upgrade Traefik
to recreate its `LoadBalancer` Service, and upsert both DNS records to the new
external IP. Restore PostgreSQL from the current daily S3 slot before running
acceptance tests. The Kafka topic hook recreates all seven topics; Kafka data
is intentionally not restored.

### First deep-sleep result (7 August 2026)

Before shutdown, job `postgres-backup-deep-sleep-20260807-r1` uploaded and
verified `postgres/daily-5.dump` at 150,462 bytes. The independent Helm restore
test succeeded with 47 user tables. Both preproduction A records still pointed
to `91.134.64.82` and were removed in the OVHcloud Manager; no apex, `www`, MX,
SPF, DKIM or other mail record was changed. The scoped Public Cloud API token
correctly rejected DNS access, so it was not broadened for this one operation.

All five application Deployments were scaled to zero. The Kafka and PostgreSQL
PVCs and their dynamically provisioned 20 GiB volumes were deleted, followed
by the Traefik `LoadBalancer` Service. The reviewed Terraform plan SHA-256 was
`a6fb6fd01399065db06cbc36ac82157a2adde4a88f4f90c0a8894f97d468174a` and
contained exactly zero additions, one in-place CPU pool update and three
deletions. Applying it removed the Gateway, Registry bootstrap user and SMALL
Registry, and set the CPU pool minimum/desired size to zero.

Final verification reported zero Kubernetes nodes, PVCs, PVs and LoadBalancer
Services; all application Deployments were `0/0`. The OVHcloud API reported
zero GRA11 instances, volumes and Load Balancers. Terraform state still
contains the MKS cluster, zero-node CPU and GPU pools, private network/subnet,
all three protected S3 buckets and their scoped identities, but no Registry or
Gateway. A final authenticated Terraform plan returned `No changes`.

On 8 August 2026, a follow-up audit found that the CPU pool autoscaler had
recreated one `b3-8` node even though its configured minimum and desired sizes
were zero. Pending Traefik and cert-manager workloads remained eligible to
trigger scale-up. Deep-sleep mode therefore also disables autoscaling on both
node pools; a live authenticated plan and OVHcloud/Kubernetes inventory must
confirm zero instances and zero nodes after every shutdown.

The corrective plan SHA-256 was
`cfabd4d6917a0aad1d35044c9321eed44ee981ab82cab41ef66a9b0b11bcfc8c` and
contained zero additions, two in-place node-pool updates and zero deletions.
After it completed, the OVHcloud API reported zero instances, volumes and
GRA11 Load Balancers; Kubernetes reported zero nodes, PVs, PVCs and
LoadBalancer Services. A final authenticated Terraform plan returned
`No changes`.

## 11. Acceptance and rollback

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

The Terraform state bucket is never part of ordinary teardown. Deleting either
retained bucket is a separate, deliberate OVHcloud operation after its contents
and recovery requirements have been reviewed.

### Digest-only CPU deployment helper

For a new CPU release, pass the two full published references as `API_IMAGE`
and `FRONTEND_IMAGE`. Supply the qualified executor configuration through
`RELEASE_VALUES_FILE` (the ignored local overlay above); CPU-first deployment
still needs valid executor digests even when the GPU Jobs remain pending.

```bash
API_IMAGE="<registry>/droneai/drone-dashboard-api@sha256:<64-hex-digest>" \
FRONTEND_IMAGE="<registry>/droneai/drone-dashboard-frontend@sha256:<64-hex-digest>" \
RELEASE_VALUES_FILE="charts/drone-ai/values-ovh-preprod.local.yaml" \
scripts/deploy/deploy-ovh-preprod-cpu.sh
```

With both image variables omitted, the helper reuses the deployed API and
frontend digests and checks that the control worker runs the same API image.
A deployment still using tags is rejected: supply both qualified digest
references explicitly for its migration. `IMAGE_TAG` no longer controls
cloud deployment. Local `deploy.sh distributed --stage-jobs GIT_SHA` retains
its development-only tag contract.


### Network policy qualification

The protected overlay enables application default-deny policies and sets
`networkPolicy.ingressNamespace=traefik`. Before rollout, confirm namespace
labels and exercise: frontend/API ingress, Prometheus metrics from
`networkPolicy.monitoringNamespace`, DNS, PostgreSQL, Kafka, S3, model
downloads, control-worker Job creation and a complete Stage Job. Confirm a
Stage Job cannot reach Kafka or the Kubernetes API. Port 443 remains
destination-agnostic because standard NetworkPolicy cannot match S3/model DNS
names; record any CNI-specific FQDN restriction separately.
