resource "aws_ssm_parameter" "api_key" {
  name        = "/zeus/eia/api_key"
  description = "EIA API key. Set out-of-band via aws ssm put-parameter."
  type        = "SecureString"
  value       = "PLACEHOLDER_SET_VIA_CLI"

  lifecycle {
    ignore_changes = [value]
  }
}
