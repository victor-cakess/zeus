locals {
  project = "zeus"
  env     = "dev"

  prefix             = "${local.project}-${local.env}"
  state_machine_name = "${local.prefix}-daily-pipeline"

  alerts_topic_arn = data.terraform_remote_state.core.outputs.alerts_topic_arn

  dbt_function_arn    = data.terraform_remote_state.dbt.outputs.function_arn
  digest_function_arn = data.terraform_remote_state.digest.outputs.digest_function_arn

  # One entry per fan-out ingest source (function ARNs + unit lists come from each
  # pipeline's state — single source of truth, no duplication). The state machine's
  # Branches, the CheckFailures Choice rules, and the SFN role's
  # lambda:InvokeFunction list are ALL derived from this list — adding a source is
  # adding an entry here (plus its remote_state data source). List order defines
  # branch order and $.ingest[i]. dbt/digest stay out: they are pipeline stages,
  # not fan-out sources.
  ingest_sources = [
    {
      name         = "eia"
      function_arn = data.terraform_remote_state.eia.outputs.ingest_function_arn
      units        = data.terraform_remote_state.eia.outputs.balancing_authorities
    },
    {
      name         = "eia_region"
      function_arn = data.terraform_remote_state.eia_region.outputs.ingest_function_arn
      units        = data.terraform_remote_state.eia_region.outputs.balancing_authorities
    },
    {
      name         = "noaa"
      function_arn = data.terraform_remote_state.noaa.outputs.ingest_function_arn
      units        = data.terraform_remote_state.noaa.outputs.balancing_authorities
    },
    {
      name         = "fred"
      function_arn = data.terraform_remote_state.fred.outputs.ingest_function_arn
      units        = data.terraform_remote_state.fred.outputs.series
    },
  ]

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
