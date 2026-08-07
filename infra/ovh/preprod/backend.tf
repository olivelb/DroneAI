terraform {
  # Credentials are loaded from AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY.
  # All non-secret OVH S3 settings are supplied by the reviewed .tfbackend file.
  backend "s3" {}
}
