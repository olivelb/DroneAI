locals {
  terraform_state_key = "preprod/terraform.tfstate"
}

# Bootstrap resource: this bucket is created while the stack still uses its
# protected local state. The backend is enabled only after this resource and
# its dedicated credentials have been applied and verified.
resource "ovh_cloud_project_storage" "terraform_state" {
  service_name = var.project_id
  region_name  = var.object_storage_region
  name         = var.terraform_state_bucket
  hide_objects = true

  encryption = {
    sse_algorithm = "AES256"
  }
  versioning = {
    status = "enabled"
  }
  tags = merge(local.tags, {
    purpose = "terraform-state"
  })

  lifecycle {
    prevent_destroy = true
  }
}

resource "ovh_cloud_project_user" "terraform_state" {
  service_name = var.project_id
  description  = "DroneAI ${var.environment} Terraform state backend"
  role_names   = ["objectstore_operator"]
}

resource "ovh_cloud_project_user_s3_credential" "terraform_state" {
  service_name = ovh_cloud_project_user.terraform_state.service_name
  user_id      = ovh_cloud_project_user.terraform_state.id
}

# The backend account can list this dedicated bucket, update the state object
# without deleting it, and remove only the short-lived lock object.
resource "ovh_cloud_project_user_s3_policy" "terraform_state" {
  service_name = ovh_cloud_project_user.terraform_state.service_name
  user_id      = ovh_cloud_project_user.terraform_state.id
  policy = jsonencode({
    Statement = [
      {
        Sid      = "InspectStateBucket"
        Effect   = "Allow"
        Action   = ["s3:GetBucketLocation", "s3:ListBucket"]
        Resource = ["arn:aws:s3:::${ovh_cloud_project_storage.terraform_state.name}"]
      },
      {
        Sid      = "ReadWriteStateWithoutDelete"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = ["arn:aws:s3:::${ovh_cloud_project_storage.terraform_state.name}/${local.terraform_state_key}"]
      },
      {
        Sid      = "ManageStateLock"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = ["arn:aws:s3:::${ovh_cloud_project_storage.terraform_state.name}/${local.terraform_state_key}.tflock"]
      },
    ]
  })
}
