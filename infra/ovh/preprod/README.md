# OVHcloud preproduction infrastructure

This Terraform stack describes, but does not automatically apply, the first
DroneAI preproduction platform in OVHcloud project
`fe7dc1254a9847849e0d29b01fe39b22`.

It creates:

- a `GRA11` private network, subnet and small managed gateway;
- a free-plan OVHcloud Managed Kubernetes Service cluster;
- one autoscaled `b3-8` CPU pool, limited to two nodes;
- an optional GPU pool that starts at zero and is disabled by default;
- a SMALL Managed Private Registry in `GRA`;
- an encrypted, versioned Object Storage bucket protected by
  `prevent_destroy`.

Authentication is read only from `OVH_*` environment variables. Terraform
state contains the MKS client key and must be treated as a secret. The complete,
ordered runbook is in [`../../../docs/OVHCLOUD_PREPROD.md`](../../../docs/OVHCLOUD_PREPROD.md).

Static validation, which creates no OVHcloud resource:

```bash
cp terraform.tfvars.example terraform.tfvars
terraform init -backend=false
terraform fmt -check
terraform validate
```

Do not run `terraform apply` until the plan, GPU flavor, quota and expected
hourly cost have been reviewed. The data bucket cannot be destroyed by a normal
Terraform destroy operation.
