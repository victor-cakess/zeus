resource "aws_iam_role" "sfn" {
  name = "${local.prefix}-eia-sfn"

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
  name = "${local.prefix}-eia-sfn"
  role = aws_iam_role.sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = [
        aws_lambda_function.this.arn,
        aws_lambda_function.transform.arn,
      ]
    }]
  })
}

resource "aws_sfn_state_machine" "this" {
  name     = local.sfn_name
  role_arn = aws_iam_role.sfn.arn
  type     = "STANDARD"

  definition = jsonencode({
    Comment = "Fan out one EIA Lambda invocation per balancing authority, then consolidate to Parquet."
    StartAt = "FanOut"
    States = {
      FanOut = {
        Type           = "Map"
        ItemsPath      = "$.respondents"
        MaxConcurrency = 20
        ItemProcessor = {
          ProcessorConfig = { Mode = "INLINE" }
          StartAt         = "InvokeEIA"
          States = {
            InvokeEIA = {
              Type     = "Task"
              Resource = "arn:aws:states:::lambda:invoke"
              Parameters = {
                FunctionName = aws_lambda_function.this.arn
                Payload = {
                  "respondent.$" = "$"
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
        }
        Next = "Consolidate"
      }
      Consolidate = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.transform.arn
          Payload      = {}
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
