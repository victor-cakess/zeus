resource "aws_ssm_parameter" "api_key" {
  name        = local.ssm_key_path
  description = "${var.source_name} API key. Set out-of-band via aws ssm put-parameter."
  type        = "SecureString"
  value       = "PLACEHOLDER_SET_VIA_CLI"

  lifecycle {
    ignore_changes = [value]
  }
}
