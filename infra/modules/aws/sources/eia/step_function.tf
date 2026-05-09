resource "aws_iam_role" "sfn" {
  name = "zeus-eia-sfn"

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
  name = "zeus-eia-sfn"
  role = aws_iam_role.sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.this.arn
    }]
  })
}

resource "aws_sfn_state_machine" "this" {
  name     = "zeus-eia-daily"
  role_arn = aws_iam_role.sfn.arn
  type     = "STANDARD"

  definition = jsonencode({
    Comment = "Fan out one EIA Lambda invocation per balancing authority."
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
        End = true
      }
    }
  })
}
