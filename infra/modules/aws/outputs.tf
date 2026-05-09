output "bucket_name" {
  description = "Name of the S3 data bucket."
  value       = aws_s3_bucket.data.bucket
}

output "eia_lambda_function_name" {
  description = "Name of the EIA extraction Lambda function."
  value       = module.eia.lambda_function_name
}

output "eia_state_machine_arn" {
  description = "ARN of the EIA daily Step Function."
  value       = module.eia.state_machine_arn
}

output "eia_api_key_ssm_path" {
  description = "SSM Parameter Store path holding the EIA API key. Set value via aws ssm put-parameter."
  value       = module.eia.api_key_ssm_path
}
