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

variable "eia_loader_public_key" {
  type        = string
  description = "RSA public key body (no PEM header/footer) for the ZEUS_DEV_EIA_LOADER service user. The matching private key is set out-of-band in SSM; nothing secret here."
}

variable "noaa_loader_public_key" {
  type        = string
  description = "RSA public key body (no PEM header/footer) for the ZEUS_DEV_NOAA_LOADER service user. The matching private key is set out-of-band in SSM; nothing secret here."
}

variable "fred_loader_public_key" {
  type        = string
  description = "RSA public key body (no PEM header/footer) for the ZEUS_DEV_FRED_LOADER service user. The matching private key is set out-of-band in SSM; nothing secret here."
}

variable "transformer_public_key" {
  type        = string
  description = "RSA public key body (no PEM header/footer) for the ZEUS_DEV_TRANSFORMER service user (dbt). The matching private key stays local for dbt runs; nothing secret here."
}

variable "ci_public_key" {
  type        = string
  description = "RSA public key body (no PEM header/footer) for the ZEUS_DEV_CI service user (GitHub Actions dbt clone CI). The matching private key lives in GitHub Actions secrets; nothing secret here."
}

variable "dashboard_public_key" {
  type        = string
  description = "RSA public key body (no PEM header/footer) for the ZEUS_DEV_DASHBOARD service user (public Streamlit dashboard). The matching private key lives in Streamlit Community Cloud secrets, NOT SSM; nothing secret here."
}
