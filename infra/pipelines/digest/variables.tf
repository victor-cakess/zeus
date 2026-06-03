variable "schedule_cron" {
  type    = string
  default = "cron(0 8 * * ? *)" # after EIA (07:00) and NOAA (07:30) ingest runs
}

variable "sources" {
  description = "Sources whose run reports the digest summarizes, in email order."
  type        = list(string)
  default     = ["eia", "noaa"]
}
