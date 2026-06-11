terraform {
  backend "local" {}

  # Swap to this block for CI/CD remote state (remove backend "local" above):
  # backend "s3" {
  #   bucket         = "zeus-analytics-tfstate"
  #   key            = "cicd/terraform.tfstate"
  #   region         = "sa-east-1"
  #   dynamodb_table = "zeus-tfstate-locks"
  #   encrypt        = true
  # }
}
