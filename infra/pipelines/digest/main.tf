locals {
  prefix      = "${local.project}-${local.env}"
  digest_name = "${local.prefix}-reports-digest"

  bucket_name      = data.terraform_remote_state.core.outputs.bucket_name
  bucket_arn       = data.terraform_remote_state.core.outputs.bucket_arn
  alerts_topic_arn = data.terraform_remote_state.core.outputs.alerts_topic_arn

  reports_arn_pattern = "${local.bucket_arn}/reports/*"
}

# Daily digest Lambda: after both ingest pipelines run, reads each source's
# run_report.json for the day and emails one combined summary. Replaces the
# per-pipeline success emails (the ingest Lambdas keep only their on-failure alerts).
# Adding a future source = append it to var.sources.
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

# On-failure safety net: a crash in the digest itself routes to the alerts topic.
resource "aws_lambda_function_event_invoke_config" "digest" {
  function_name          = module.digest.function_name
  maximum_retry_attempts = 0

  destination_config {
    on_failure {
      destination = local.alerts_topic_arn
    }
  }
}

# Daily trigger, after both ingest runs. No payload — the handler reads SOURCES.
resource "aws_cloudwatch_event_rule" "daily" {
  name                = "${local.prefix}-reports-digest-daily"
  description         = "Daily cross-source run-report digest email."
  schedule_expression = var.schedule_cron
}

resource "aws_cloudwatch_event_target" "daily" {
  rule      = aws_cloudwatch_event_rule.daily.name
  target_id = "reports-digest"
  arn       = module.digest.function_arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = module.digest.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily.arn
}
