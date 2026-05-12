data "terraform_remote_state" "core" {
  backend = "local"
  config = {
    path = "${path.module}/../../core/terraform.tfstate"
  }
}
