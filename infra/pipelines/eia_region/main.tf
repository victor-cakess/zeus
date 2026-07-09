locals {
  prefix      = "${local.project}-${local.env}"
  ingest_name = "${local.prefix}-eia-region-ingest"

  # Shared with the EIA fuel-type pipeline: same API, same key. The eia root OWNS
  # the SSM parameter (authors it, exports its path); this root only consumes the
  # path — one Terraform owner per secret, so neither root can clobber the other.
  api_key_ssm_path = "/${local.project}/${local.env}/eia/api_key"
  api_key_arn      = "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter${local.api_key_ssm_path}"

  snowflake_key_ssm_path = "/${local.project}/${local.env}/snowflake/eia_region_loader_private_key"

  bucket_name      = data.terraform_remote_state.core.outputs.bucket_name
  bucket_arn       = data.terraform_remote_state.core.outputs.bucket_arn
  alerts_topic_arn = data.terraform_remote_state.core.outputs.alerts_topic_arn

  raw_arn_pattern     = "${local.bucket_arn}/raw/eia_region/*"
  curated_arn_pattern = "${local.bucket_arn}/curated/eia_region/*"
  reports_arn_pattern = "${local.bucket_arn}/reports/eia_region/*"
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Snowflake loader private key (PKCS8 PEM), set out-of-band via `aws ssm
# put-parameter`. The matching public key lives on the ZEUS_DEV_EIA_REGION_LOADER user.
resource "aws_ssm_parameter" "snowflake_key" {
  name        = local.snowflake_key_ssm_path
  description = "ZEUS_DEV_EIA_REGION_LOADER RSA private key. Set out-of-band via aws ssm put-parameter."
  type        = "SecureString"
  value       = "PLACEHOLDER_SET_VIA_CLI"

  lifecycle {
    ignore_changes = [value]
  }
}

# Single ingest Lambda: fetches every BA in parallel (one request-loop per BA
# returns all four region-data series: D, DF, NG, TI), drops raw JSON, then
# consolidates the day's partition to one curated Parquet, loads it into Snowflake,
# and writes the run report (the daily digest Lambda emails the combined summary).
# Invoked synchronously by the daily state machine (infra/pipelines/orchestration),
# which owns scheduling and failure alerting.
module "ingest" {
  source = "../../modules/lambda_job"

  name            = local.ingest_name
  src_dir         = "${path.module}/../../../src/lambdas/eia_region/ingest"
  shared_dir      = "${path.module}/../../../src/shared"
  build_dir       = "${path.module}/../../build/${local.ingest_name}"
  zip_path        = "${path.module}/../../build/${local.ingest_name}.zip"
  artifact_bucket = local.bucket_name

  memory_size = 1024
  timeout     = 300

  env_vars = {
    BUCKET           = local.bucket_name
    SOURCE           = "eia_region"
    API_KEY_SSM_PATH = local.api_key_ssm_path
    LOOKBACK_DAYS    = tostring(var.lookback_days)
    MAX_WORKERS      = "20"

    SNOWFLAKE_ACCOUNT              = data.terraform_remote_state.core.outputs.snowflake_account
    SNOWFLAKE_USER                 = "ZEUS_DEV_EIA_REGION_LOADER"
    SNOWFLAKE_ROLE                 = "ZEUS_DEV_EIA_REGION_LOADER_ROLE"
    SNOWFLAKE_WAREHOUSE            = data.terraform_remote_state.core.outputs.snowflake_warehouse_name
    SNOWFLAKE_DATABASE             = data.terraform_remote_state.core.outputs.snowflake_database_name
    SNOWFLAKE_SCHEMA               = "EIA_REGION"
    SNOWFLAKE_TABLE                = "EIA_REGION_GRID"
    SNOWFLAKE_STAGE                = "EIA_REGION_STAGE"
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
      Resource = [local.raw_arn_pattern, local.reports_arn_pattern]
    },
    {
      Effect   = "Allow"
      Action   = "s3:ListBucket"
      Resource = local.bucket_arn
      Condition = {
        StringLike = {
          "s3:prefix" = ["raw/eia_region/*", "reports/eia_region/*"]
        }
      }
    },
    {
      Effect   = "Allow"
      Action   = "ssm:GetParameter"
      Resource = [local.api_key_arn, aws_ssm_parameter.snowflake_key.arn]
    },
    {
      Effect   = "Allow"
      Action   = "kms:Decrypt"
      Resource = "*"
      Condition = {
        StringEquals = {
          "kms:EncryptionContext:PARAMETER_ARN" = [
            local.api_key_arn,
            aws_ssm_parameter.snowflake_key.arn,
          ]
        }
      }
    },
  ]
}
