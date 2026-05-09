resource "null_resource" "lambda_deps" {
  triggers = {
    requirements = filemd5("${local.lambda_src_dir}/requirements.txt")
    handler      = filemd5("${local.lambda_src_dir}/handler.py")
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      unset VIRTUAL_ENV
      rm -rf ${local.lambda_build_dir}
      mkdir -p ${local.lambda_build_dir}
      uv pip install --quiet --python python3.12 --target ${local.lambda_build_dir} -r ${local.lambda_src_dir}/requirements.txt
      cp ${local.lambda_src_dir}/handler.py ${local.lambda_build_dir}/
    EOT
  }
}

data "archive_file" "this" {
  type        = "zip"
  source_dir  = local.lambda_build_dir
  output_path = "${path.module}/../../build/eia.zip"

  depends_on = [null_resource.lambda_deps]
}

resource "aws_iam_role" "lambda" {
  name = "${local.prefix}-eia-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda" {
  name = "${local.prefix}-eia-lambda"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = "${data.aws_s3_bucket.data.arn}/raw/eia/*"
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
      }
    ]
  })
}

resource "aws_lambda_function" "this" {
  function_name    = local.lambda_name
  role             = aws_iam_role.lambda.arn
  filename         = data.archive_file.this.output_path
  source_code_hash = data.archive_file.this.output_base64sha256
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  memory_size      = 512
  timeout          = 300

  environment {
    variables = {
      BUCKET               = data.aws_s3_bucket.data.id
      EIA_API_KEY_SSM_PATH = local.ssm_key_path
      LOOKBACK_DAYS        = tostring(var.lookback_days)
    }
  }
}
