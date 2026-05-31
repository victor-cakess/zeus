locals {
  extract_name   = "${var.prefix}-${var.source_name}-extract"
  transform_name = "${var.prefix}-${var.source_name}-transform"
  ssm_key_path   = "/${var.project}/${var.env}/${var.source_name}/api_key"

  raw_arn_pattern     = "${var.bucket_arn}/raw/${var.source_name}/*"
  curated_arn_pattern = "${var.bucket_arn}/curated/${var.source_name}/*"
  reports_arn_pattern = "${var.bucket_arn}/reports/${var.source_name}/*"

  common_env = {
    BUCKET = var.bucket_name
    SOURCE = var.source_name
  }
}

module "extract" {
  source = "../lambda_job"

  name       = local.extract_name
  src_dir    = var.extract_src_dir
  shared_dir = var.shared_src_dir
  build_dir  = "${var.build_root}/${local.extract_name}"
  zip_path   = "${var.build_root}/${local.extract_name}.zip"

  env_vars = merge(local.common_env, {
    API_KEY_SSM_PATH = local.ssm_key_path
    LOOKBACK_DAYS    = tostring(var.lookback_days)
  })

  policy_statements = [
    {
      Effect   = "Allow"
      Action   = "s3:PutObject"
      Resource = local.raw_arn_pattern
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
  ]
}

module "transform" {
  source = "../lambda_job"

  name       = local.transform_name
  src_dir    = var.transform_src_dir
  shared_dir = var.shared_src_dir
  build_dir  = "${var.build_root}/${local.transform_name}"
  zip_path   = "${var.build_root}/${local.transform_name}.zip"

  env_vars = merge(local.common_env, {
    SNS_TOPIC_ARN     = var.alerts_topic_arn
    SKIP_HISTORY_DAYS = "30"
  })

  policy_statements = [
    {
      Effect   = "Allow"
      Action   = "s3:ListBucket"
      Resource = var.bucket_arn
      Condition = {
        StringLike = {
          "s3:prefix" = ["raw/${var.source_name}/*", "reports/${var.source_name}/*"]
        }
      }
    },
    {
      Effect   = "Allow"
      Action   = "s3:GetObject"
      Resource = [local.raw_arn_pattern, local.reports_arn_pattern]
    },
    {
      Effect   = "Allow"
      Action   = "s3:PutObject"
      Resource = [local.curated_arn_pattern, local.reports_arn_pattern]
    },
    {
      Effect   = "Allow"
      Action   = "sns:Publish"
      Resource = var.alerts_topic_arn
    },
  ]
}
