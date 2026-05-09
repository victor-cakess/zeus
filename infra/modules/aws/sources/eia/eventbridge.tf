resource "aws_iam_role" "eventbridge" {
  name = "zeus-eia-eventbridge"

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
  name = "zeus-eia-eventbridge"
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
  name                = "zeus-eia-daily"
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
