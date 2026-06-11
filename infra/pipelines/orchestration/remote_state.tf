data "terraform_remote_state" "core" {
  backend = "local"
  config = {
    path = "${path.module}/../../core/terraform.tfstate"
  }
}

data "terraform_remote_state" "eia" {
  backend = "local"
  config = {
    path = "${path.module}/../eia/terraform.tfstate"
  }
}

data "terraform_remote_state" "noaa" {
  backend = "local"
  config = {
    path = "${path.module}/../noaa/terraform.tfstate"
  }
}

data "terraform_remote_state" "fred" {
  backend = "local"
  config = {
    path = "${path.module}/../fred/terraform.tfstate"
  }
}

data "terraform_remote_state" "digest" {
  backend = "local"
  config = {
    path = "${path.module}/../digest/terraform.tfstate"
  }
}

data "terraform_remote_state" "dbt" {
  backend = "local"
  config = {
    path = "${path.module}/../dbt/terraform.tfstate"
  }
}
