resource "aws_cloudwatch_event_rule" "sfn_failure" {
  name        = "${var.prefix}-${var.source_name}-sfn-failure"
  description = "Fires when the ${var.source_name} Step Function execution fails, times out, or is aborted."

  event_pattern = jsonencode({
    source      = ["aws.states"]
    detail-type = ["Step Functions Execution Status Change"]
    detail = {
      status          = ["FAILED", "TIMED_OUT", "ABORTED"]
      stateMachineArn = [aws_sfn_state_machine.this.arn]
    }
  })
}

resource "aws_cloudwatch_event_target" "sfn_failure" {
  rule      = aws_cloudwatch_event_rule.sfn_failure.name
  target_id = "${var.source_name}-sfn-failure-sns"
  arn       = var.alerts_topic_arn

  input_transformer {
    input_paths = {
      execution = "$.detail.executionArn"
      status    = "$.detail.status"
      started   = "$.detail.startDate"
      stopped   = "$.detail.stopDate"
    }
    input_template = "\"${upper(var.source_name)} pipeline <status>\\n\\nExecution: <execution>\\nStarted:   <started>\\nStopped:   <stopped>\\n\\nFor failure details, run:\\n  aws stepfunctions describe-execution --execution-arn <execution>\""
  }
}
