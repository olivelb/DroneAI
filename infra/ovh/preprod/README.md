# OVHcloud preproduction infrastructure

This Terraform stack describes, but does not automatically apply, the first
DroneAI preproduction platform in OVHcloud project
`fe7dc1254a9847849e0d29b01fe39b22`.

It creates:

- a `GRA11` private network, subnet and small managed gateway;
- a free-plan OVHcloud Managed Kubernetes Service cluster;
- one autoscaled `b3-8` CPU pool, limited to two nodes;
- an optional autoscaled GPU pool that starts at zero and is disabled by
  default (`l4-90`, maximum one, in the applied preproduction configuration);
- a SMALL Managed Private Registry in `GRA`;
- a bootstrap Harbor account used to create the private `droneai` project;
- an encrypted, versioned Object Storage bucket protected by
  `prevent_destroy`;
- a dedicated application S3 account restricted to that assets bucket;
- a separate encrypted PostgreSQL backup bucket protected by `prevent_destroy`,
  with a dedicated account limited to `GetObject`/`PutObject` under
  `postgres/*` and no object deletion permission;
- a second encrypted, versioned bucket dedicated to Terraform state, with a
  separate S3 account restricted to the state and lock objects.

Setting `deep_sleep = true` retains the free MKS control plane, private network,
protected S3 buckets and their scoped identities while scaling the CPU pool to
zero, disabling autoscaling on both node pools, and removing the hourly billed
Gateway and Managed Private Registry. Disabling autoscaling is required because
pending auxiliary workloads can otherwise recreate a CPU node after the first
scale-to-zero operation. The Kubernetes LoadBalancer and dynamically
provisioned PVCs must be removed using the ordered runbook before applying that
Terraform plan.

Authentication is read only from `OVH_*` environment variables. Terraform
state contains the MKS client key, Harbor password and S3 credentials and must
be treated as a secret. The complete, ordered runbook, including the two-phase
remote-state migration, is in
[`../../../docs/OVHCLOUD_PREPROD.md`](../../../docs/OVHCLOUD_PREPROD.md).

Static validation, which creates no OVHcloud resource:

```bash
cp terraform.tfvars.example terraform.tfvars
terraform init -backend=false
terraform fmt -check
terraform validate
```

For an authenticated plan against the deployed environment, source the local
OVH and backend credential files and initialize with
`backend-preprod.s3.tfbackend.example`. The backend credentials are never
passed through command-line arguments.

Do not run `terraform apply` until the plan, GPU flavor, quota and expected
hourly cost have been reviewed. The data and state buckets cannot be destroyed
by a normal Terraform destroy operation. Remote S3 state and native lock-file
contention were verified on 7 August 2026.

The applied preproduction environment additionally has the zero-node `l4-90`
pool and the backup bucket enabled. The GPU pool cannot create its first node
until GRA11 RAM quota is raised from 44 GB to at least 98 GB. A zero-node pool
has no GPU compute charge. PostgreSQL backup storage is bounded by seven daily
object keys; the Kubernetes CronJob and real disposable restore test are
defined in the DroneAI Helm chart.
