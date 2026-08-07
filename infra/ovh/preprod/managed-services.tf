resource "ovh_cloud_project_containerregistry_user" "bootstrap" {
  service_name = var.project_id
  registry_id  = ovh_cloud_project_containerregistry.preprod.id
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
