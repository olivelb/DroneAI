resource "ovh_cloud_project_containerregistry_user" "bootstrap" {
  count = var.deep_sleep ? 0 : 1

  service_name = var.project_id
  registry_id  = ovh_cloud_project_containerregistry.preprod[0].id
  email        = "admin@olembo.fr"
  login        = "droneai-bootstrap"
}

resource "ovh_cloud_project_user" "assets" {
  service_name = var.project_id
  description  = "DroneAI ${var.environment} application assets"
  role_names   = ["objectstore_operator"]
}

resource "ovh_cloud_project_user_s3_credential" "assets" {
  service_name = ovh_cloud_project_user.assets.service_name
  user_id      = ovh_cloud_project_user.assets.id
}

resource "ovh_cloud_project_user_s3_policy" "assets" {
  service_name = ovh_cloud_project_user.assets.service_name
  user_id      = ovh_cloud_project_user.assets.id
  policy = jsonencode({
    Statement = [
      {
        Sid      = "InspectAssetsBucket"
        Effect   = "Allow"
        Action   = ["s3:GetBucketLocation", "s3:ListBucket", "s3:ListBucketMultipartUploads"]
        Resource = ["arn:aws:s3:::${ovh_cloud_project_storage.assets.name}"]
      },
      {
        Sid    = "ManageAssetsObjects"
        Effect = "Allow"
        Action = [
          "s3:AbortMultipartUpload",
          "s3:DeleteObject",
          "s3:GetObject",
          "s3:ListMultipartUploadParts",
          "s3:PutObject",
        ]
        Resource = ["arn:aws:s3:::${ovh_cloud_project_storage.assets.name}/*"]
      },
    ]
  })
}

resource "ovh_cloud_project_user" "backups" {
  service_name = var.project_id
  description  = "DroneAI ${var.environment} PostgreSQL backups"
  role_names   = ["objectstore_operator"]
}

resource "ovh_cloud_project_user_s3_credential" "backups" {
  service_name = ovh_cloud_project_user.backups.service_name
  user_id      = ovh_cloud_project_user.backups.id
}

resource "ovh_cloud_project_user_s3_policy" "backups" {
  service_name = ovh_cloud_project_user.backups.service_name
  user_id      = ovh_cloud_project_user.backups.id
  policy = jsonencode({
    Statement = [
      {
        Sid      = "InspectBackupBucket"
        Effect   = "Allow"
        Action   = ["s3:GetBucketLocation", "s3:ListBucket"]
        Resource = ["arn:aws:s3:::${ovh_cloud_project_storage.backups.name}"]
      },
      {
        Sid      = "RotateBackupObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = ["arn:aws:s3:::${ovh_cloud_project_storage.backups.name}/postgres/*"]
      },
    ]
  })
}

locals {
  stage_identities = toset([
    "reconstruction",
    "gaussian_training",
    "gaussian_filtering",
    "rasterization",
    "detection",
    "gaussian_viewer",
  ])
}

resource "ovh_cloud_project_user" "stage" {
  for_each = local.stage_identities

  service_name = var.project_id
  description  = "DroneAI ${var.environment} ${each.key} Stage executor"
  role_names   = ["objectstore_operator"]
}

resource "ovh_cloud_project_user_s3_credential" "stage" {
  for_each = local.stage_identities

  service_name = ovh_cloud_project_user.stage[each.key].service_name
  user_id      = ovh_cloud_project_user.stage[each.key].id
}

resource "ovh_cloud_project_user_s3_policy" "stage" {
  for_each = local.stage_identities

  service_name = ovh_cloud_project_user.stage[each.key].service_name
  user_id      = ovh_cloud_project_user.stage[each.key].id
  policy = jsonencode({
    Statement = [
      {
        Sid      = "InspectStageAssetsBucket"
        Effect   = "Allow"
        Action   = ["s3:GetBucketLocation", "s3:ListBucket", "s3:ListBucketMultipartUploads"]
        Resource = ["arn:aws:s3:::${ovh_cloud_project_storage.assets.name}"]
      },
      {
        Sid    = "ReadWriteStageAssetsWithoutDelete"
        Effect = "Allow"
        Action = [
          "s3:AbortMultipartUpload",
          "s3:GetObject",
          "s3:ListMultipartUploadParts",
          "s3:PutObject",
        ]
        Resource = ["arn:aws:s3:::${ovh_cloud_project_storage.assets.name}/*"]
      },
    ]
  })
}
