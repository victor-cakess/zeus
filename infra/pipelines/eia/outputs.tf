output "lambda_function_name" {
  value = aws_lambda_function.this.function_name
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.this.arn
}

output "api_key_ssm_path" {
  value = aws_ssm_parameter.api_key.name
}