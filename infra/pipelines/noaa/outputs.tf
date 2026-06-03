output "ingest_function_name" {
  value = module.ingest.function_name
}

output "snowflake_key_ssm_path" {
  value = aws_ssm_parameter.snowflake_key.name
}
