# CI/CD deploy identity: a GitHub Actions OIDC provider + a least-privilege role the
# dbt-deploy workflow assumes to build/push the dbt image and update the Lambda. No
# long-lived AWS keys in GitHub — the workflow exchanges its OIDC token for short-lived
# creds, and only the dev branch of this repo can assume the role.
#
# This is the "CI owns the image" half of the deploy split: Terraform still provisions
# the dbt Lambda/ECR (infra/pipelines/dbt), but the image build + update-function-code
# moved out of `terraform apply` into .github/workflows/dbt-deploy.yml. Apply this root
# once; it has no ongoing coupling to the pipeline roots.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  function_name = "zeus-dev-dbt-run"
  account_id    = data.aws_caller_identity.current.account_id
  region        = data.aws_region.current.name

  ecr_repo_arn = "arn:aws:ecr:${local.region}:${local.account_id}:repository/${local.function_name}"
  function_arn = "arn:aws:lambda:${local.region}:${local.account_id}:function:${local.function_name}"
}

# GitHub's OIDC token signing cert — thumbprint fetched live, not hardcoded (it rotates).
data "tls_certificate" "github" {
  url = "https://token.actions.githubusercontent.com"
}

# Account-singleton (keyed by URL). If one already exists, `terraform import` it instead
# of applying this resource.
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github.certificates[length(data.tls_certificate.github.certificates) - 1].sha1_fingerprint]
}

resource "aws_iam_role" "dbt_deploy" {
  name = "zeus-dev-dbt-deploy"

  # Trust: only OIDC tokens from this repo's dev branch (sub claim) with the STS
  # audience may assume the role.
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:ref:refs/heads/${var.deploy_branch}"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "dbt_deploy" {
  name = "zeus-dev-dbt-deploy"
  role = aws_iam_role.dbt_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # ECR login token — can't be resource-scoped.
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        # Push (and pull, for layer caching) on the dbt repo only.
        Sid    = "EcrPushPull"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Resource = local.ecr_repo_arn
      },
      {
        # Repoint + smoke-invoke the dbt Lambda only.
        Sid    = "LambdaDeploy"
        Effect = "Allow"
        Action = [
          "lambda:UpdateFunctionCode",
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration",
          "lambda:InvokeFunction",
        ]
        Resource = local.function_arn
      },
    ]
  })
}
