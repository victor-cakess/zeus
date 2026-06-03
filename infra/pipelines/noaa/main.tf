locals {
  prefix                 = "${local.project}-${local.env}"
  ingest_name            = "${local.prefix}-noaa-ingest"
  snowflake_key_ssm_path = "/${local.project}/${local.env}/snowflake/noaa_loader_private_key"

  bucket_name      = data.terraform_remote_state.core.outputs.bucket_name
  bucket_arn       = data.terraform_remote_state.core.outputs.bucket_arn
  alerts_topic_arn = data.terraform_remote_state.core.outputs.alerts_topic_arn

  raw_arn_pattern     = "${local.bucket_arn}/raw/noaa/*"
  curated_arn_pattern = "${local.bucket_arn}/curated/noaa/*"
  reports_arn_pattern = "${local.bucket_arn}/reports/noaa/*"
}

# Snowflake loader private key (PKCS8 PEM), set out-of-band via `aws ssm
# put-parameter`. The matching public key lives on the ZEUS_DEV_NOAA_LOADER user.
# NOAA's NCEI endpoint needs no API key, so there's no api_key parameter here.
resource "aws_ssm_parameter" "snowflake_key" {
  name        = local.snowflake_key_ssm_path
  description = "ZEUS_DEV_NOAA_LOADER RSA private key. Set out-of-band via aws ssm put-parameter."
  type        = "SecureString"
  value       = "PLACEHOLDER_SET_VIA_CLI"

  lifecycle {
    ignore_changes = [value]
  }
}

# Single ingest Lambda: fetches every BA's stations in parallel, drops raw JSON,
# then consolidates the day's partition to one curated Parquet, loads it into
# Snowflake, and writes the run report (the daily digest Lambda emails the summary).
module "ingest" {
  source = "../../modules/lambda_job"

  name            = local.ingest_name
  src_dir         = "${path.module}/../../../src/lambdas/noaa/ingest"
  shared_dir      = "${path.module}/../../../src/shared"
  build_dir       = "${path.module}/../../build/${local.ingest_name}"
  zip_path        = "${path.module}/../../build/${local.ingest_name}.zip"
  artifact_bucket = local.bucket_name

  memory_size = 1024
  timeout     = 300

  env_vars = {
    BUCKET        = local.bucket_name
    SOURCE        = "noaa"
    LOOKBACK_DAYS = tostring(var.lookback_days)
    MAX_WORKERS   = "20"

    SNOWFLAKE_ACCOUNT              = data.terraform_remote_state.core.outputs.snowflake_account
    SNOWFLAKE_USER                 = "ZEUS_DEV_NOAA_LOADER"
    SNOWFLAKE_ROLE                 = "ZEUS_DEV_NOAA_LOADER_ROLE"
    SNOWFLAKE_WAREHOUSE            = data.terraform_remote_state.core.outputs.snowflake_warehouse_name
    SNOWFLAKE_DATABASE             = data.terraform_remote_state.core.outputs.snowflake_database_name
    SNOWFLAKE_SCHEMA               = "NOAA"
    SNOWFLAKE_TABLE                = "NOAA_GRID"
    SNOWFLAKE_STAGE                = "NOAA_STAGE"
    SNOWFLAKE_PRIVATE_KEY_SSM_PATH = local.snowflake_key_ssm_path
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
      Resource = [local.raw_arn_pattern]
    },
    {
      Effect   = "Allow"
      Action   = "s3:ListBucket"
      Resource = local.bucket_arn
      Condition = {
        StringLike = {
          "s3:prefix" = ["raw/noaa/*"]
        }
      }
    },
    {
      Effect   = "Allow"
      Action   = "ssm:GetParameter"
      Resource = [aws_ssm_parameter.snowflake_key.arn]
    },
    {
      Effect   = "Allow"
      Action   = "kms:Decrypt"
      Resource = "*"
      Condition = {
        StringEquals = {
          "kms:EncryptionContext:PARAMETER_ARN" = [
            aws_ssm_parameter.snowflake_key.arn,
          ]
        }
      }
    },
    # Needed for the Lambda's async on-failure destination, not in-handler publish.
    {
      Effect   = "Allow"
      Action   = "sns:Publish"
      Resource = local.alerts_topic_arn
    },
  ]
}

# Hard-failure safety net: EventBridge invokes the Lambda asynchronously, so any
# crash (OOM, timeout, init error, total outage) routes the failed event to the
# alerts topic. No retries — one shot.
resource "aws_lambda_function_event_invoke_config" "ingest" {
  function_name          = module.ingest.function_name
  maximum_retry_attempts = 0

  destination_config {
    on_failure {
      destination = local.alerts_topic_arn
    }
  }
}

# Daily trigger — invokes the Lambda directly with the BA list.
resource "aws_cloudwatch_event_rule" "daily" {
  name                = "${local.prefix}-noaa-daily"
  description         = "Daily trigger for NOAA ingestion."
  schedule_expression = var.schedule_cron
}

resource "aws_cloudwatch_event_target" "daily" {
  rule      = aws_cloudwatch_event_rule.daily.name
  target_id = "noaa-daily-ingest"
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
