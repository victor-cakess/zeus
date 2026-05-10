variable "aws_region" {
  type    = string
  default = "sa-east-1"
}

variable "lookback_days" {
  type    = number
  default = 7
}

variable "schedule_cron" {
  type    = string
  default = "cron(0 7 * * ? *)"
}