output "lambda_function_name" {
  description = "Name of the EIA extraction Lambda function."
  value       = aws_lambda_function.this.function_name
}

output "state_machine_arn" {
  description = "ARN of the EIA daily Step Function."
  value       = aws_sfn_state_machine.this.arn
}

output "api_key_ssm_path" {
  description = "SSM Parameter Store path holding the EIA API key."
  value       = aws_ssm_parameter.api_key.name
}
