resource "aws_iam_role" "eventbridge" {
  name = "${local.prefix}-eia-eventbridge"

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
  name = "${local.prefix}-eia-eventbridge"
  role = aws_iam_role.eventbridge.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "states:StartExecution"
      Resource = aws_sfn_state_machine.this.arn
    }]
  })
}

resource "aws_cloudwatch_event_rule" "daily" {
  name                = local.eb_rule_name
  description         = "Daily trigger for EIA ingestion Step Function."
  schedule_expression = var.schedule_cron
}

resource "aws_cloudwatch_event_target" "daily" {
  rule      = aws_cloudwatch_event_rule.daily.name
  target_id = "eia-daily-sfn"
  arn       = aws_sfn_state_machine.this.arn
  role_arn  = aws_iam_role.eventbridge.arn

  input = jsonencode({
    respondents = local.balancing_authorities
  })
}

resource "aws_cloudwatch_event_rule" "sfn_failure" {
  name        = "${local.prefix}-eia-sfn-failure"
  description = "Fires when the EIA Step Function execution fails, times out, or is aborted."

  event_pattern = jsonencode({
    source      = ["aws.states"]
    detail-type = ["Step Functions Execution Status Change"]
    detail = {
      status         = ["FAILED", "TIMED_OUT", "ABORTED"]
      stateMachineArn = [aws_sfn_state_machine.this.arn]
    }
  })
}

resource "aws_cloudwatch_event_target" "sfn_failure" {
  rule      = aws_cloudwatch_event_rule.sfn_failure.name
  target_id = "eia-sfn-failure-sns"
  arn       = data.terraform_remote_state.core.outputs.alerts_topic_arn

  input_transformer {
    input_paths = {
      execution = "$.detail.executionArn"
      status    = "$.detail.status"
      started   = "$.detail.startDate"
      stopped   = "$.detail.stopDate"
    }
    input_template = "\"EIA pipeline <status>\\n\\nExecution: <execution>\\nStarted:   <started>\\nStopped:   <stopped>\\n\\nFor failure details, run:\\n  aws stepfunctions describe-execution --execution-arn <execution>\""
  }
}
