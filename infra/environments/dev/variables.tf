variable "organization_name" {
  type        = string
  description = "Snowflake organization name."
}

variable "account_name" {
  type        = string
  description = "Snowflake account name."
}

variable "user" {
  type        = string
  description = "Snowflake user."
}

variable "password" {
  type        = string
  description = "Snowflake user password."
  sensitive   = true
}

variable "role" {
  type        = string
  description = "Snowflake role to assume."
  default     = "SYSADMIN"
}

variable "warehouse_name" {
  type        = string
  description = "Name of the Snowflake warehouse to create."
  default     = "ZEUS_DEV_WH"
}

variable "warehouse_size" {
  type        = string
  description = "Size of the Snowflake warehouse."
  default     = "x-small"
}

variable "aws_region" {
  type        = string
  description = "AWS region for data infrastructure."
  default     = "sa-east-1"
}

variable "data_bucket_name" {
  type        = string
  description = "Name of the S3 data bucket."
  default     = "s3-energy-data-bucket"
}
