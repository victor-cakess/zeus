locals {
  project = "zeus"
  env     = "dev"

  prefix             = "${local.project}-${local.env}"
  state_machine_name = "${local.prefix}-daily-pipeline"

  alerts_topic_arn = data.terraform_remote_state.core.outputs.alerts_topic_arn

  eia_function_arn    = data.terraform_remote_state.eia.outputs.ingest_function_arn
  noaa_function_arn   = data.terraform_remote_state.noaa.outputs.ingest_function_arn
  dbt_function_arn    = data.terraform_remote_state.dbt.outputs.function_arn
  digest_function_arn = data.terraform_remote_state.digest.outputs.digest_function_arn

  # BA lists come from each pipeline's state — single source of truth, no duplication.
  eia_balancing_authorities  = data.terraform_remote_state.eia.outputs.balancing_authorities
  noaa_balancing_authorities = data.terraform_remote_state.noaa.outputs.balancing_authorities

  # Retry only AWS-transient invoke failures; a function error (crash, total outage)
  # goes straight to the branch Catch — retrying a code bug just doubles the run.
  lambda_transient_retry = [{
    ErrorEquals = [
      "Lambda.ServiceException",
      "Lambda.TooManyRequestsException",
      "Lambda.SdkClientException",
      "Lambda.AWSLambdaException",
    ]
    IntervalSeconds = 2
    MaxAttempts     = 2
    BackoffRate     = 2.0
  }]
}
