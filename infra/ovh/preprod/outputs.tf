output "cluster_id" {
  description = "Managed Kubernetes cluster identifier."
  value       = ovh_cloud_project_kube.preprod.id
}

output "gateway_public_ips" {
  description = "Stable egress IP information, useful for managed-service allowlists."
  value       = ovh_cloud_project_gateway.preprod.external_information
}

output "registry_host" {
  description = "Managed Private Registry hostname for docker login."
  value = trimsuffix(
    trimprefix(
      trimprefix(ovh_cloud_project_containerregistry.preprod.url, "https://"),
      "http://",
    ),
    "/",
  )
}

output "registry_project_prefix" {
  description = "Immutable image prefix to use as global.imageRegistry."
  value = "${trimsuffix(
    trimprefix(
      trimprefix(ovh_cloud_project_containerregistry.preprod.url, "https://"),
      "http://",
    ),
    "/",
  )}/droneai/"
}

output "registry_bootstrap_login" {
  description = "Temporary administrative login used to initialize the Harbor project."
  value       = ovh_cloud_project_containerregistry_user.bootstrap.user
}

output "registry_bootstrap_password" {
  description = "Temporary Harbor bootstrap password; store it locally and never commit it."
  value       = ovh_cloud_project_containerregistry_user.bootstrap.password
  sensitive   = true
}

output "object_storage_endpoint" {
  description = "Regional S3 API endpoint used by boto3 clients."
  value       = "https://s3.${lower(var.object_storage_region)}.io.cloud.ovh.net"
}

output "object_storage_virtual_host" {
  description = "Bucket-specific HTTPS virtual host reported by OVHcloud."
  value = startswith(ovh_cloud_project_storage.assets.virtual_host, "http") ? (
    ovh_cloud_project_storage.assets.virtual_host
  ) : "https://${ovh_cloud_project_storage.assets.virtual_host}"
}

output "object_storage_bucket" {
  value = ovh_cloud_project_storage.assets.name
}

output "object_storage_access_key_id" {
  description = "Dedicated application S3 access key; store it locally and never commit it."
  value       = ovh_cloud_project_user_s3_credential.assets.access_key_id
  sensitive   = true
}

output "object_storage_secret_access_key" {
  description = "Dedicated application S3 secret; store it locally and never commit it."
  value       = ovh_cloud_project_user_s3_credential.assets.secret_access_key
  sensitive   = true
}

output "terraform_state_bucket" {
  description = "Dedicated encrypted and versioned S3 backend bucket."
  value       = ovh_cloud_project_storage.terraform_state.name
}

output "terraform_state_endpoint" {
  description = "Regional S3 endpoint used by the Terraform backend."
  value       = "https://s3.${lower(var.object_storage_region)}.io.cloud.ovh.net"
}

output "terraform_backend_access_key_id" {
  description = "Dedicated S3 backend access key; export it locally and never commit it."
  value       = ovh_cloud_project_user_s3_credential.terraform_state.access_key_id
  sensitive   = true
}

output "terraform_backend_secret_access_key" {
  description = "Dedicated S3 backend secret; export it locally and never commit it."
  value       = ovh_cloud_project_user_s3_credential.terraform_state.secret_access_key
  sensitive   = true
}

# Sensitive because the provider stores client certificates and a private key
# inside the kubeconfig. Write it only to a chmod 0600 file.
output "kubeconfig" {
  value       = ovh_cloud_project_kube.preprod.kubeconfig
  sensitive   = true
  description = "Kubeconfig for the preproduction MKS cluster."
}
