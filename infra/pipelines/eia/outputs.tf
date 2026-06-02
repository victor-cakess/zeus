output "ingest_function_name" {
  value = module.ingest.function_name
}

output "api_key_ssm_path" {
  value = aws_ssm_parameter.api_key.name
}
