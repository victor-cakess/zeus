locals {
  sfn_name = "${var.prefix}-${var.source_name}-daily"
}

resource "aws_iam_role" "sfn" {
  name = "${var.prefix}-${var.source_name}-sfn"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "sfn" {
  name = "${var.prefix}-${var.source_name}-sfn"
  role = aws_iam_role.sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "lambda:InvokeFunction"
      Resource = [
        module.extract.function_arn,
        module.transform.function_arn,
      ]
    }]
  })
}

resource "aws_sfn_state_machine" "this" {
  name     = local.sfn_name
  role_arn = aws_iam_role.sfn.arn
  type     = "STANDARD"

  definition = jsonencode({
    Comment = "Fan out one ${var.source_name} extract per unit, then consolidate to Parquet."
    StartAt = "FanOut"
    States = {
      FanOut = {
        Type           = "Map"
        ItemsPath      = "$.units"
        MaxConcurrency = var.max_concurrency
        ItemSelector = {
          "unit.$" = "$$.Map.Item.Value"
        }
        ItemProcessor = {
          ProcessorConfig = { Mode = "INLINE" }
          StartAt         = "InvokeExtract"
          States = {
            InvokeExtract = {
              Type     = "Task"
              Resource = "arn:aws:states:::lambda:invoke"
              Parameters = {
                FunctionName = module.extract.function_arn
                Payload = {
                  "unit.$" = "$.unit"
                }
              }
              ResultSelector = {
                "unit.$" = "$.Payload.unit"
                "rows.$" = "$.Payload.rows"
                "status" = "ok"
              }
              Retry = [{
                ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"]
                IntervalSeconds = 5
                MaxAttempts     = 2
                BackoffRate     = 2.0
              }]
              Catch = [{
                ErrorEquals = ["States.ALL"]
                ResultPath  = "$.errorInfo"
                Next        = "RecordSkip"
              }]
              End = true
            }
            RecordSkip = {
              Type = "Pass"
              Parameters = {
                "unit.$"  = "$.unit"
                "status"  = "skipped"
                "error.$" = "$.errorInfo.Cause"
              }
              End = true
            }
          }
        }
        Next = "Consolidate"
      }
      Consolidate = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = module.transform.function_arn
          Payload = {
            "results.$" = "$"
          }
        }
        Retry = [{
          ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"]
          IntervalSeconds = 5
          MaxAttempts     = 2
          BackoffRate     = 2.0
        }]
        End = true
      }
    }
  })
}
