# Daily pipeline state machine: ingest EIA + NOAA in parallel, run dbt (build +
# tests), then run the digest. Each ingest branch catches its own failure and the
# dbt step's Catch routes straight to the digest, so the digest ALWAYS runs; a
# final Choice alerts + fails the execution if any step failed, so a bad day is
# red in the console and lands in the inbox.

# Vended-logs prefix keeps the CloudWatch resource policy small (AWS recommendation
# for Step Functions logging).
resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/vendedlogs/states/${local.state_machine_name}"
  retention_in_days = 30
}

resource "aws_sfn_state_machine" "daily" {
  name     = local.state_machine_name
  role_arn = aws_iam_role.sfn.arn

  definition = jsonencode({
    Comment = "Zeus daily pipeline: parallel EIA + NOAA ingest, then digest. Digest always runs."
    StartAt = "Ingest"
    States = {
      Ingest = {
        Type = "Parallel"
        # Branch order is load-bearing: CheckFailures reads $.ingest[0] (EIA)
        # and $.ingest[1] (NOAA).
        ResultPath = "$.ingest"
        Branches = [
          {
            StartAt = "IngestEIA"
            States = {
              IngestEIA = {
                Type     = "Task"
                Resource = "arn:aws:states:::lambda:invoke"
                Parameters = {
                  FunctionName = local.eia_function_arn
                  Payload      = { units = local.eia_balancing_authorities }
                }
                Retry = local.lambda_transient_retry
                Catch = [{
                  ErrorEquals = ["States.ALL"]
                  ResultPath  = "$.errorInfo"
                  Next        = "EIAFail"
                }]
                Next = "EIAOk"
              }
              # Ok/Fail Pass states normalize every branch result to {source, failed}
              # so the CheckFailures Choice never references a missing path.
              EIAOk = {
                Type   = "Pass"
                Result = { source = "eia", failed = false }
                End    = true
              }
              EIAFail = {
                Type = "Pass"
                Parameters = {
                  source    = "eia"
                  failed    = true
                  "error.$" = "$.errorInfo.Error"
                  "cause.$" = "$.errorInfo.Cause"
                }
                End = true
              }
            }
          },
          {
            StartAt = "IngestNOAA"
            States = {
              IngestNOAA = {
                Type     = "Task"
                Resource = "arn:aws:states:::lambda:invoke"
                Parameters = {
                  FunctionName = local.noaa_function_arn
                  Payload      = { units = local.noaa_balancing_authorities }
                }
                Retry = local.lambda_transient_retry
                Catch = [{
                  ErrorEquals = ["States.ALL"]
                  ResultPath  = "$.errorInfo"
                  Next        = "NOAAFail"
                }]
                Next = "NOAAOk"
              }
              NOAAOk = {
                Type   = "Pass"
                Result = { source = "noaa", failed = false }
                End    = true
              }
              NOAAFail = {
                Type = "Pass"
                Parameters = {
                  source    = "noaa"
                  failed    = true
                  "error.$" = "$.errorInfo.Error"
                  "cause.$" = "$.errorInfo.Cause"
                }
                End = true
              }
            }
          },
        ]
        Next = "Dbt"
      }
      # dbt build (models + tests). A failure — including failing tests — still
      # runs the digest (the handler writes its run report before raising, so the
      # email carries the failed-test names), then CheckFailures alerts.
      Dbt = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = local.dbt_function_arn
          Payload      = {}
        }
        Retry          = local.lambda_transient_retry
        ResultSelector = { "payload.$" = "$.Payload" }
        ResultPath     = "$.dbt"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.dbtError"
          Next        = "Digest"
        }]
        Next = "Digest"
      }
      # Runs unconditionally — a crashed ingest shows up in the email as "no report".
      Digest = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = local.digest_function_arn
          Payload      = {}
        }
        Retry          = local.lambda_transient_retry
        ResultSelector = { "payload.$" = "$.Payload" }
        ResultPath     = "$.digest"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.digestError"
          Next        = "CheckFailures"
        }]
        Next = "CheckFailures"
      }
      CheckFailures = {
        Type = "Choice"
        Choices = [
          { Variable = "$.ingest[0].failed", BooleanEquals = true, Next = "NotifyFailure" },
          { Variable = "$.ingest[1].failed", BooleanEquals = true, Next = "NotifyFailure" },
          { Variable = "$.dbtError", IsPresent = true, Next = "NotifyFailure" },
          { Variable = "$.digestError", IsPresent = true, Next = "NotifyFailure" },
        ]
        Default = "Success"
      }
      NotifyFailure = {
        Type     = "Task"
        Resource = "arn:aws:states:::sns:publish"
        Parameters = {
          TopicArn    = local.alerts_topic_arn
          Subject     = "[${local.prefix}] daily pipeline FAILED"
          "Message.$" = "States.JsonToString($)"
        }
        Next = "FailExecution"
      }
      FailExecution = {
        Type  = "Fail"
        Error = "DailyPipelineFailed"
        Cause = "One or more steps failed. See the SNS alert and the execution history."
      }
      Success = { Type = "Succeed" }
    }
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tracing_configuration {
    enabled = true
  }
}

# --- State machine execution role ---

resource "aws_iam_role" "sfn" {
  name = local.state_machine_name

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
  name = local.state_machine_name
  role = aws_iam_role.sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "lambda:InvokeFunction"
        Resource = [
          local.eia_function_arn,
          local.noaa_function_arn,
          local.dbt_function_arn,
          local.digest_function_arn,
        ]
      },
      {
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = local.alerts_topic_arn
      },
      # CloudWatch Logs delivery for state-machine logging; these actions don't
      # support resource-level scoping.
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups",
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets",
        ]
        Resource = "*"
      },
    ]
  })
}

# --- Daily trigger ---

resource "aws_iam_role" "eventbridge" {
  name = "${local.state_machine_name}-trigger"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "eventbridge" {
  name = "${local.state_machine_name}-trigger"
  role = aws_iam_role.eventbridge.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "states:StartExecution"
      Resource = aws_sfn_state_machine.daily.arn
    }]
  })
}

resource "aws_cloudwatch_event_rule" "daily" {
  name                = local.state_machine_name
  description         = "Daily trigger for the Zeus pipeline state machine."
  schedule_expression = var.schedule_cron
}

resource "aws_cloudwatch_event_target" "daily" {
  rule      = aws_cloudwatch_event_rule.daily.name
  target_id = "daily-pipeline"
  arn       = aws_sfn_state_machine.daily.arn
  role_arn  = aws_iam_role.eventbridge.arn

  # Empty execution input — the BA payloads are baked into the state machine.
  input = jsonencode({})
}
