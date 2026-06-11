output "deploy_role_arn" {
  description = "Role ARN the dbt-deploy workflow assumes. Set as the GitHub Actions variable AWS_DEPLOY_ROLE_ARN."
  value       = aws_iam_role.dbt_deploy.arn
}
