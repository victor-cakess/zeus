module "eia" {
  source = "./sources/eia"

  bucket_arn    = aws_s3_bucket.data.arn
  bucket_name   = aws_s3_bucket.data.bucket
  lookback_days = var.eia_lookback_days
  schedule_cron = var.eia_schedule_cron
}
