variable "aws_region" {
  type    = string
  default = "sa-east-1"
}

variable "organization_name" {
  type = string
}

variable "account_name" {
  type = string
}

variable "user" {
  type = string
}

variable "password" {
  type      = string
  sensitive = true
}

variable "role" {
  type    = string
  default = "SYSADMIN"
}

variable "warehouse_name" {
  type    = string
  default = "ZEUS_DEV_WH"
}

variable "warehouse_size" {
  type    = string
  default = "x-small"
}

variable "alert_email" {
  type        = string
  description = "Email address to receive pipeline failure alerts."
}
