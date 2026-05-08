terraform {
  backend "s3" {
    bucket         = "zeus-analytics-tfstate"
    key            = "environments/dev/terraform.tfstate"
    region         = "sa-east-1"
    dynamodb_table = "zeus-tfstate-locks"
    encrypt        = true
  }
}
