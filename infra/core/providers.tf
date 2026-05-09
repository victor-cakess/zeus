terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    snowflake = {
      source  = "Snowflake-Labs/snowflake"
      version = "~> 0.98"
    }
  }
  required_version = ">= 1.5"
}

provider "aws" {
  region = var.aws_region
}

provider "snowflake" {
  organization_name = var.organization_name
  account_name      = var.account_name
  user              = var.user
  password          = var.password
  role              = var.role
}
