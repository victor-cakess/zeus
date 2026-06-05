output "bucket_name" {
  value = aws_s3_bucket.data.id
}

output "bucket_arn" {
  value = aws_s3_bucket.data.arn
}

output "snowflake_warehouse_name" {
  value = snowflake_warehouse.this.name
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

# Account identifier in org-account form, e.g. WYCCXHS-KUB52402 — consumed by
# pipelines that connect to Snowflake (the snowflake-connector `account` param).
output "snowflake_account" {
  value = "${var.organization_name}-${var.account_name}"
}

output "snowflake_database_name" {
  value = snowflake_database.zeus_dev.name
}

# dbt connects as this service user / role (key-pair auth) to build the modeled layers.
output "snowflake_transformer_user" {
  value = snowflake_service_user.transformer.name
}

output "snowflake_transformer_role" {
  value = snowflake_account_role.transformer.name
}
