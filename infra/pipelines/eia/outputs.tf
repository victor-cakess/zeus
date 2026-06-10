output "ingest_function_name" {
  value = module.ingest.function_name
}

output "ingest_function_arn" {
  value = module.ingest.function_arn
}

# Single source of truth for the EIA BA list — the orchestration root reads this
# to build the state machine's invoke payload.
output "balancing_authorities" {
  value = local.balancing_authorities
}

output "api_key_ssm_path" {
  value = aws_ssm_parameter.api_key.name
}
