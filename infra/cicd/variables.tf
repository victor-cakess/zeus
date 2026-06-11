variable "github_repo" {
  description = "GitHub repo (owner/name) allowed to assume the deploy role via OIDC."
  type        = string
  default     = "victor-cakess/zeus"
}

variable "deploy_branch" {
  description = "Only this branch's workflow runs may assume the deploy role (sub claim)."
  type        = string
  default     = "dev"
}
