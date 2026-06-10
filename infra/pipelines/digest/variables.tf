variable "sources" {
  description = "Sources whose run reports the digest summarizes, in email order."
  type        = list(string)
  default     = ["eia", "noaa"]
}
