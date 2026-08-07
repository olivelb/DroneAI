variable "project_id" {
  description = "OVHcloud Public Cloud project identifier."
  type        = string
  sensitive   = false

  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.project_id))
    error_message = "project_id must be the 32-character OVHcloud Public Cloud project ID."
  }
}

variable "region" {
  description = "OVHcloud Public Cloud region for MKS and its node pools."
  type        = string
  default     = "GRA11"
}

variable "object_storage_region" {
  description = "OVHcloud Object Storage region name."
  type        = string
  default     = "GRA"
}

variable "environment" {
  description = "Environment tag shared by the provisioned resources."
  type        = string
  default     = "preprod"
}

variable "private_network_cidr" {
  description = "Private subnet CIDR. Do not use OVH MKS-reserved 10.2.0.0/16 or 10.3.0.0/16."
  type        = string
  default     = "10.20.0.0/24"
}

variable "private_network_pool_start" {
  description = "First DHCP address in the private subnet."
  type        = string
  default     = "10.20.0.20"
}

variable "private_network_pool_end" {
  description = "Last DHCP address in the private subnet."
  type        = string
  default     = "10.20.0.220"
}

variable "cpu_flavor" {
  description = "General-purpose node flavor. b3-8 fits the current GRA11 quota."
  type        = string
  default     = "b3-8"
}

variable "cpu_desired_nodes" {
  description = "Initial number of CPU nodes."
  type        = number
  default     = 1

  validation {
    condition     = var.cpu_desired_nodes >= 1 && var.cpu_desired_nodes <= 2
    error_message = "Preproduction is deliberately limited to one or two CPU nodes."
  }
}

variable "enable_gpu_pool" {
  description = "Create the GPU node pool definition. Nodes still start at zero and scale only for matching workloads."
  type        = bool
  default     = false
}

variable "gpu_flavor" {
  description = "GPU flavor confirmed available in the selected region; required when enable_gpu_pool is true."
  type        = string
  default     = ""

  validation {
    condition     = !var.enable_gpu_pool || length(trimspace(var.gpu_flavor)) > 0
    error_message = "Set gpu_flavor only after confirming its GRA11 availability and quota."
  }
}

variable "gpu_max_nodes" {
  description = "Maximum GPU nodes. One permits one exclusive GPU worker; two permit COLMAP and IA concurrently."
  type        = number
  default     = 1

  validation {
    condition     = var.gpu_max_nodes >= 1 && var.gpu_max_nodes <= 2
    error_message = "Preproduction is deliberately limited to one or two GPU nodes."
  }
}

variable "registry_plan" {
  description = "OVHcloud Managed Private Registry plan."
  type        = string
  default     = "SMALL"
}

variable "object_storage_bucket" {
  description = "Globally unique S3 bucket name used by DroneAI."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.object_storage_bucket))
    error_message = "Use a 3-63 character, lower-case S3-compatible bucket name."
  }
}

variable "terraform_state_bucket" {
  description = "Globally unique encrypted and versioned S3 bucket used only by the Terraform backend."
  type        = string
  default     = "droneai-preprod-tfstate-fe7dc125"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.terraform_state_bucket))
    error_message = "Use a 3-63 character, lower-case S3-compatible bucket name."
  }
}
