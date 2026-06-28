terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    snowflake = {
      # Pinned exactly: the provider is pre-1.0 and changes how it renders/reads back
      # attributes (e.g. snowflake_stage.file_format) between minors, which surfaces as
      # phantom drift. Bump deliberately, not by floating.
      source  = "Snowflake-Labs/snowflake"
      version = "0.100.0"
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
