output "extract_function_name" {
  value = module.pipeline.extract_function_name
}

output "transform_function_name" {
  value = module.pipeline.transform_function_name
}

output "state_machine_arn" {
  value = module.pipeline.state_machine_arn
}

output "api_key_ssm_path" {
  value = module.pipeline.api_key_ssm_path
}
