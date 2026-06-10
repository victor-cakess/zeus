variable "schedule_cron" {
  type    = string
  default = "cron(0 7 * * ? *)" # 07:00 UTC = 04:00 sa-east-1
}
