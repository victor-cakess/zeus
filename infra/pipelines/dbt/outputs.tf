output "function_arn" {
  value = aws_lambda_function.dbt.arn
}

output "function_name" {
  value = aws_lambda_function.dbt.function_name
}

output "transformer_key_ssm_path" {
  value = aws_ssm_parameter.transformer_key.name
}
