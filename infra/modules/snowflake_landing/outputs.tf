output "schema_name" {
  value = snowflake_schema.this.name
}

output "table_name" {
  value = snowflake_table.this.name
}

output "stage_name" {
  value = snowflake_stage.this.name
}

output "loader_user_name" {
  value = snowflake_service_user.this.name
}

output "loader_role_name" {
  value = snowflake_account_role.this.name
}

output "iam_role_arn" {
  value = aws_iam_role.this.arn
}

output "integration_name" {
  value = snowflake_storage_integration.this.name
}
