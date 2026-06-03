variable "schedule_cron" {
  type    = string
  default = "cron(30 7 * * ? *)" # 30 min after EIA, to stagger the two daily runs
}

variable "lookback_days" {
  type    = number
  default = 7
}
