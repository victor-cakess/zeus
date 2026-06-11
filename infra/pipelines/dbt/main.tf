# dbt runner: a container-image Lambda (dbt-snowflake doesn't fit a zip) that runs
# `dbt build` (models + tests) against ZEUS_DEV as the transformer user, writes a
# run report to reports/dbt/, and raises on failure so the state machine catches it.
# Invoked synchronously by the daily state machine (infra/pipelines/orchestration).

resource "aws_ecr_repository" "dbt" {
  name = local.function_name
}

# Keep only the last few images so storage stays pennies.
resource "aws_ecr_lifecycle_policy" "dbt" {
  repository = aws_ecr_repository.dbt.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep last 3 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 3
      }
      action = { type = "expire" }
    }]
  })
}

# NOTE: the image build/push moved out of Terraform into CI
# (.github/workflows/dbt-deploy.yml) — that workflow owns the image, this root owns the
# Lambda infra (see lifecycle.ignore_changes on aws_lambda_function.dbt below). The
# image_uri here is bootstrap-only: it pins the tag at create time, then CI's
# update-function-code takes over and Terraform ignores the drift. For a from-scratch
# environment, push one image first (run dbt-deploy via workflow_dispatch, or a manual
# docker build/push) so the tag exists before this Lambda is created.

# Transformer private key (PKCS8 PEM), set out-of-band via `aws ssm put-parameter`.
# The matching public key lives on the ZEUS_DEV_TRANSFORMER user. Manual local dbt
# runs keep using the local sf_transformer.p8 file; the Lambda fetches this.
resource "aws_ssm_parameter" "transformer_key" {
  name        = local.transformer_key_ssm_path
  description = "ZEUS_DEV_TRANSFORMER RSA private key. Set out-of-band via aws ssm put-parameter."
  type        = "SecureString"
  value       = "PLACEHOLDER_SET_VIA_CLI"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_iam_role" "dbt" {
  name = local.function_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "basic" {
  role       = aws_iam_role.dbt.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "dbt" {
  name = local.function_name
  role = aws_iam_role.dbt.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = [local.reports_arn_pattern]
      },
      {
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = [aws_ssm_parameter.transformer_key.arn]
      },
      {
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:EncryptionContext:PARAMETER_ARN" = [
              aws_ssm_parameter.transformer_key.arn,
            ]
          }
        }
      },
    ]
  })
}

resource "aws_lambda_function" "dbt" {
  function_name = local.function_name
  role          = aws_iam_role.dbt.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.dbt.repository_url}:${local.image_hash}"
  memory_size   = 2048
  timeout       = 300

  environment {
    variables = {
      BUCKET = local.bucket_name

      SNOWFLAKE_ACCOUNT              = data.terraform_remote_state.core.outputs.snowflake_account
      SNOWFLAKE_USER                 = "ZEUS_DEV_TRANSFORMER"
      SNOWFLAKE_ROLE                 = "ZEUS_DEV_TRANSFORMER_ROLE"
      SNOWFLAKE_WAREHOUSE            = data.terraform_remote_state.core.outputs.snowflake_warehouse_name
      SNOWFLAKE_DATABASE             = data.terraform_remote_state.core.outputs.snowflake_database_name
      SNOWFLAKE_PRIVATE_KEY_SSM_PATH = local.transformer_key_ssm_path
    }
  }

  # CI (.github/workflows/dbt-deploy.yml) owns the image via update-function-code;
  # ignore its tag here so `terraform apply` never reverts a CI deploy.
  lifecycle {
    ignore_changes = [image_uri]
  }
}
