output "ingest_function_name" {
  value = module.ingest.function_name
}

output "ingest_function_arn" {
  value = module.ingest.function_arn
}

# Single source of truth for the FRED series list — the orchestration root reads
# this to build the state machine's invoke payload.
output "series" {
  value = local.series
}

output "snowflake_key_ssm_path" {
  value = aws_ssm_parameter.snowflake_key.name
}

output "api_key_ssm_path" {
  value = aws_ssm_parameter.api_key.name
}
