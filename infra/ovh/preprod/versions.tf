terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    ovh = {
      source  = "ovh/ovh"
      version = "~> 2.18.0"
    }
  }
}

# Authentication is intentionally environment-only:
# OVH_ENDPOINT, OVH_APPLICATION_KEY, OVH_APPLICATION_SECRET and
# OVH_CONSUMER_KEY. Never put them in a tfvars file.
provider "ovh" {}
