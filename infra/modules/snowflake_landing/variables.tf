variable "source_name" {
  description = "Data source slug, lower-case (e.g. \"eia\", \"noaa\"). Drives every derived name and the curated/<source>/ S3 location."
  type        = string
}

variable "project" {
  type = string
}

variable "env" {
  type = string
}

variable "prefix" {
  description = "Resource name prefix, e.g. \"zeus-dev\". Used for the AWS IAM role name and (upper-cased, underscored) the Snowflake object prefix."
  type        = string
}

variable "bucket_name" {
  description = "Shared data bucket name (from infra/core)."
  type        = string
}

variable "bucket_arn" {
  description = "Shared data bucket ARN (from infra/core)."
  type        = string
}

variable "account_id" {
  description = "AWS account id, for the storage integration's role ARN."
  type        = string
}

variable "database_name" {
  description = "Shared Snowflake database (e.g. ZEUS_DEV). The source gets its own schema within it."
  type        = string
}

variable "warehouse_name" {
  description = "Shared Snowflake warehouse the loader uses (e.g. ZEUS_DEV_WH)."
  type        = string
}

variable "columns" {
  description = "Ordered landing-table columns, mirroring the source's pyarrow schema."
  type = list(object({
    name = string
    type = string
  }))
}

variable "loader_public_key" {
  description = "RSA public key body for the source's least-privilege key-pair loader user. Private key lives in SSM."
  type        = string
}

variable "schema_comment" {
  type = string
}

variable "table_comment" {
  type = string
}
