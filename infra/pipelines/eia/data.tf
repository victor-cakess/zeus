# Discovers the shared S3 bucket provisioned by infra/core.
# infra/core must be applied before this pipeline root.
# Bucket name is derived from the same naming convention (local.bucket_name),
# not passed as a variable — no state coupling between roots.
data "aws_s3_bucket" "data" {
  bucket = local.bucket_name
}
