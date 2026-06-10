locals {
  prefix      = "${local.project}-${local.env}"
  digest_name = "${local.prefix}-reports-digest"

  bucket_name      = data.terraform_remote_state.core.outputs.bucket_name
  bucket_arn       = data.terraform_remote_state.core.outputs.bucket_arn
  alerts_topic_arn = data.terraform_remote_state.core.outputs.alerts_topic_arn

  reports_arn_pattern = "${local.bucket_arn}/reports/*"
}

# Daily digest Lambda: after both ingest pipelines run, reads each source's
# run_report.json for the day and emails one combined summary.
# Adding a future source = append it to var.sources.
# Invoked synchronously by the daily state machine (infra/pipelines/orchestration),
# which owns scheduling and failure alerting — and always runs the digest, even
# when an ingest step failed.
module "digest" {
  source = "../../modules/lambda_job"

  name            = local.digest_name
  src_dir         = "${path.module}/../../../src/lambdas/digest"
  shared_dir      = "${path.module}/../../../src/shared"
  build_dir       = "${path.module}/../../build/${local.digest_name}"
  zip_path        = "${path.module}/../../build/${local.digest_name}.zip"
  artifact_bucket = local.bucket_name

  memory_size = 256
  timeout     = 60

  env_vars = {
    BUCKET            = local.bucket_name
    SOURCES           = join(",", var.sources)
    SNS_TOPIC_ARN     = local.alerts_topic_arn
    SKIP_HISTORY_DAYS = "30"
  }

  policy_statements = [
    {
      Effect   = "Allow"
      Action   = "s3:GetObject"
      Resource = [local.reports_arn_pattern]
    },
    {
      Effect   = "Allow"
      Action   = "s3:ListBucket"
      Resource = local.bucket_arn
      Condition = {
        StringLike = {
          "s3:prefix" = ["reports/*"]
        }
      }
    },
    {
      Effect   = "Allow"
      Action   = "sns:Publish"
      Resource = local.alerts_topic_arn
    },
  ]
}
