locals {
  prefix       = "${local.project}-${local.env}"
  ingest_name  = "${local.prefix}-eia-ingest"
  ssm_key_path = "/${local.project}/${local.env}/eia/api_key"

  bucket_name      = data.terraform_remote_state.core.outputs.bucket_name
  bucket_arn       = data.terraform_remote_state.core.outputs.bucket_arn
  alerts_topic_arn = data.terraform_remote_state.core.outputs.alerts_topic_arn

  raw_arn_pattern     = "${local.bucket_arn}/raw/eia/*"
  curated_arn_pattern = "${local.bucket_arn}/curated/eia/*"
  reports_arn_pattern = "${local.bucket_arn}/reports/eia/*"
}

# API key, set out-of-band via `aws ssm put-parameter`.
resource "aws_ssm_parameter" "api_key" {
  name        = local.ssm_key_path
  description = "EIA API key. Set out-of-band via aws ssm put-parameter."
  type        = "SecureString"
  value       = "PLACEHOLDER_SET_VIA_CLI"

  lifecycle {
    ignore_changes = [value]
  }
}

# Single ingest Lambda: fetches every BA in parallel, drops raw JSON, then
# consolidates the day's partition to one curated Parquet and emails the run report.
module "ingest" {
  source = "../../modules/lambda_job"

  name       = local.ingest_name
  src_dir    = "${path.module}/../../../src/lambdas/eia/ingest"
  shared_dir = "${path.module}/../../../src/shared"
  build_dir  = "${path.module}/../../build/${local.ingest_name}"
  zip_path   = "${path.module}/../../build/${local.ingest_name}.zip"

  memory_size = 1024
  timeout     = 300

  env_vars = {
    BUCKET            = local.bucket_name
    SOURCE            = "eia"
    API_KEY_SSM_PATH  = local.ssm_key_path
    LOOKBACK_DAYS     = tostring(var.lookback_days)
    SNS_TOPIC_ARN     = local.alerts_topic_arn
    SKIP_HISTORY_DAYS = "30"
    MAX_WORKERS       = "20"
  }

  policy_statements = [
    {
      Effect   = "Allow"
      Action   = "s3:PutObject"
      Resource = [local.raw_arn_pattern, local.curated_arn_pattern, local.reports_arn_pattern]
    },
    {
      Effect   = "Allow"
      Action   = "s3:GetObject"
      Resource = [local.raw_arn_pattern, local.reports_arn_pattern]
    },
    {
      Effect   = "Allow"
      Action   = "s3:ListBucket"
      Resource = local.bucket_arn
      Condition = {
        StringLike = {
          "s3:prefix" = ["raw/eia/*", "reports/eia/*"]
        }
      }
    },
    {
      Effect   = "Allow"
      Action   = "ssm:GetParameter"
      Resource = aws_ssm_parameter.api_key.arn
    },
    {
      Effect   = "Allow"
      Action   = "kms:Decrypt"
      Resource = "*"
      Condition = {
        StringEquals = {
          "kms:EncryptionContext:PARAMETER_ARN" = aws_ssm_parameter.api_key.arn
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

# Hard-failure safety net: EventBridge invokes the Lambda asynchronously, so any
# crash the handler's own run-report email can't cover (OOM, timeout, init error,
# total outage) routes the failed event to the alerts topic. No retries — one shot.
resource "aws_lambda_function_event_invoke_config" "ingest" {
  function_name          = module.ingest.function_name
  maximum_retry_attempts = 0

  destination_config {
    on_failure {
      destination = local.alerts_topic_arn
    }
  }
}

# Daily trigger — invokes the Lambda directly with the full BA list.
resource "aws_cloudwatch_event_rule" "daily" {
  name                = "${local.prefix}-eia-daily"
  description         = "Daily trigger for EIA ingestion."
  schedule_expression = var.schedule_cron
}

resource "aws_cloudwatch_event_target" "daily" {
  rule      = aws_cloudwatch_event_rule.daily.name
  target_id = "eia-daily-ingest"
  arn       = module.ingest.function_arn

  input = jsonencode({
    units = local.balancing_authorities
  })
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = module.ingest.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily.arn
}
