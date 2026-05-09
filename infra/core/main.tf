resource "aws_s3_bucket" "data" {
  bucket = local.bucket_name

  tags = {
    Project     = local.project
    Environment = local.env
    ManagedBy   = "terraform"
  }
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "snowflake_warehouse" "this" {
  name           = var.warehouse_name
  warehouse_size = var.warehouse_size
  auto_suspend   = 60
  auto_resume    = true
}
