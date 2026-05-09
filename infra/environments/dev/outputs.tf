output "bucket_name" {
  value = module.aws.bucket_name
}

output "eia_lambda_function_name" {
  value = module.aws.eia_lambda_function_name
}

output "eia_state_machine_arn" {
  value = module.aws.eia_state_machine_arn
}

output "eia_api_key_ssm_path" {
  value = module.aws.eia_api_key_ssm_path
}
