output "bucket_name" {
  value = aws_s3_bucket.data.id
}

output "bucket_arn" {
  value = aws_s3_bucket.data.arn
}

output "snowflake_warehouse_name" {
  value = snowflake_warehouse.this.name
}
