variable "sources" {
  description = "Sources whose run reports the digest summarizes, in email order."
  type        = list(string)
  default     = ["eia", "eia_region", "noaa", "fred"]
}
