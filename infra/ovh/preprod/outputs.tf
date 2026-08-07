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

output "object_storage_endpoint" {
  description = "HTTPS S3 virtual host reported by OVHcloud."
  value = startswith(ovh_cloud_project_storage.assets.virtual_host, "http") ? (
    ovh_cloud_project_storage.assets.virtual_host
  ) : "https://${ovh_cloud_project_storage.assets.virtual_host}"
}

output "object_storage_bucket" {
  value = ovh_cloud_project_storage.assets.name
}

# Sensitive because the provider stores client certificates and a private key
# inside the kubeconfig. Write it only to a chmod 0600 file.
output "kubeconfig" {
  value       = ovh_cloud_project_kube.preprod.kubeconfig
  sensitive   = true
  description = "Kubeconfig for the preproduction MKS cluster."
}
