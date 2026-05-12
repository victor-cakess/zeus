output "extract_function_name" {
  value = module.extract.function_name
}

output "transform_function_name" {
  value = module.transform.function_name
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.this.arn
}

output "api_key_ssm_path" {
  value = aws_ssm_parameter.api_key.name
}
