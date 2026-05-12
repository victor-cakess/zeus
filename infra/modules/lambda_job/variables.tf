variable "name" {
  description = "Full Lambda function name, e.g. zeus-dev-eia-extract."
  type        = string
}

variable "src_dir" {
  description = "Absolute path to the Lambda source dir (contains handler.py + sibling .py + requirements.txt)."
  type        = string
}

variable "shared_dir" {
  description = "Absolute path to src/shared/. Copied into the build dir as shared/."
  type        = string
}

variable "build_dir" {
  description = "Absolute path where the Lambda zip contents are staged."
  type        = string
}

variable "zip_path" {
  description = "Absolute path where the Lambda zip artifact is written."
  type        = string
}

variable "handler" {
  type    = string
  default = "handler.lambda_handler"
}

variable "runtime" {
  type    = string
  default = "python3.12"
}

variable "memory_size" {
  type    = number
  default = 512
}

variable "timeout" {
  type    = number
  default = 300
}

variable "env_vars" {
  type    = map(string)
  default = {}
}

variable "policy_statements" {
  description = "List of IAM statement objects merged into the Lambda's inline policy."
  type        = any
  default     = []
}
