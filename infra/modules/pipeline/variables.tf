variable "source_name" {
  description = "Short name of the data source, e.g. eia, noaa, fred."
  type        = string
}

variable "prefix" {
  description = "Resource-name prefix, e.g. zeus-dev. Used to build full resource names."
  type        = string
}

variable "project" {
  description = "Project component of the SSM path (/{project}/{env}/{source}/api_key)."
  type        = string
}

variable "env" {
  description = "Environment component of the SSM path (/{project}/{env}/{source}/api_key)."
  type        = string
}

variable "bucket_name" {
  type = string
}

variable "bucket_arn" {
  type = string
}

variable "alerts_topic_arn" {
  type = string
}

variable "schedule_cron" {
  description = "EventBridge cron expression for the daily trigger."
  type        = string
}

variable "fanout_items" {
  description = "List of units (e.g. balancing authorities) the extract Lambda fans out over."
  type        = list(string)
}

variable "max_concurrency" {
  type    = number
  default = 20
}

variable "lookback_days" {
  type    = number
  default = 7
}

variable "extract_src_dir" {
  type = string
}

variable "transform_src_dir" {
  type = string
}

variable "shared_src_dir" {
  type = string
}

variable "build_root" {
  description = "Absolute path to the build output root (e.g. infra/build)."
  type        = string
}
