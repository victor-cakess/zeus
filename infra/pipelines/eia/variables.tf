variable "schedule_cron" {
  type    = string
  default = "cron(0 7 * * ? *)"
}

variable "lookback_days" {
  type    = number
  default = 7
}
