locals {
  name = "droneai-${var.environment}"
  tags = {
    application = "droneai"
    environment = var.environment
    managed-by  = "terraform"
  }
}

resource "ovh_cloud_project_network_private" "preprod" {
  service_name = var.project_id
  name         = "${local.name}-network"
  vlan_id      = 20
  regions      = [var.region]
}

resource "ovh_cloud_project_network_private_subnet" "preprod" {
  service_name = var.project_id
  network_id   = ovh_cloud_project_network_private.preprod.id
  region       = var.region
  network      = var.private_network_cidr
  start        = var.private_network_pool_start
  end          = var.private_network_pool_end
  dhcp         = true
  no_gateway   = false
}

resource "ovh_cloud_project_gateway" "preprod" {
  count = var.deep_sleep ? 0 : 1

  service_name = var.project_id
  name         = "${local.name}-gateway"
  model        = "s"
  region       = var.region
  network_id   = ovh_cloud_project_network_private.preprod.regions_openstack_ids[var.region]
  subnet_id    = ovh_cloud_project_network_private_subnet.preprod.id
}

resource "ovh_cloud_project_kube" "preprod" {
  service_name = var.project_id
  name         = local.name
  region       = var.region
  plan         = "free"

  private_network_id = ovh_cloud_project_network_private.preprod.regions_openstack_ids[var.region]
  nodes_subnet_id    = ovh_cloud_project_network_private_subnet.preprod.id

  private_network_configuration {
    default_vrack_gateway              = ovh_cloud_project_network_private_subnet.preprod.gateway_ip
    private_network_routing_as_default = true
  }

  depends_on = [ovh_cloud_project_gateway.preprod]
}

resource "ovh_cloud_project_kube_nodepool" "cpu" {
  service_name   = var.project_id
  kube_id        = ovh_cloud_project_kube.preprod.id
  name           = "cpu-workers"
  flavor_name    = var.cpu_flavor
  desired_nodes  = var.deep_sleep ? 0 : var.cpu_desired_nodes
  min_nodes      = var.deep_sleep ? 0 : 1
  max_nodes      = 2
  autoscale      = var.deep_sleep ? false : true
  monthly_billed = false
  anti_affinity  = false

  template {
    metadata {
      annotations = {}
      finalizers  = []
      labels = {
        "droneai.io/pool" = "cpu"
      }
    }
    spec {
      unschedulable = false
      taints        = []
    }
  }
}

resource "ovh_cloud_project_kube_nodepool" "gpu" {
  count = var.enable_gpu_pool ? 1 : 0

  service_name   = var.project_id
  kube_id        = ovh_cloud_project_kube.preprod.id
  name           = "gpu-workers"
  flavor_name    = var.gpu_flavor
  desired_nodes  = 0
  min_nodes      = 0
  max_nodes      = var.gpu_max_nodes
  autoscale      = var.deep_sleep ? false : true
  monthly_billed = false
  anti_affinity  = false

  template {
    metadata {
      annotations = {}
      finalizers  = []
      labels = {
        "droneai.io/gpu"              = "nvidia"
        "droneai.io/pool"             = "gpu"
        "droneai.io/gpu-architecture" = var.gpu_architecture
      }
    }
    spec {
      unschedulable = false
      taints = [{
        effect = "NoSchedule"
        key    = "nvidia.com/gpu"
        value  = "present"
      }]
    }
  }
}

data "ovh_cloud_project_capabilities_containerregistry_filter" "preprod" {
  service_name = var.project_id
  plan_name    = var.registry_plan
  region       = var.object_storage_region
}

resource "ovh_cloud_project_containerregistry" "preprod" {
  count = var.deep_sleep ? 0 : 1

  service_name = var.project_id
  name         = "${local.name}-registry"
  region       = data.ovh_cloud_project_capabilities_containerregistry_filter.preprod.region
  plan_id      = data.ovh_cloud_project_capabilities_containerregistry_filter.preprod.id
}

# Kept in the same initial stack for simplicity, but protected against normal
# Terraform deletion. Remove data deliberately before destroying this resource.
resource "ovh_cloud_project_storage" "assets" {
  service_name = var.project_id
  region_name  = var.object_storage_region
  name         = var.object_storage_bucket
  hide_objects = true

  encryption = {
    sse_algorithm = "AES256"
  }
  versioning = {
    status = "enabled"
  }
  tags = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

# A separate, unversioned bucket keeps seven rotating PostgreSQL dump slots
# bounded while isolating backup credentials from application assets. The
# unique daily keys are overwritten in place by the Kubernetes CronJob.
resource "ovh_cloud_project_storage" "backups" {
  service_name = var.project_id
  region_name  = var.object_storage_region
  name         = var.backup_storage_bucket
  hide_objects = true

  encryption = {
    sse_algorithm = "AES256"
  }
  tags = merge(local.tags, { component = "postgres-backups" })

  lifecycle {
    prevent_destroy = true
  }
}
