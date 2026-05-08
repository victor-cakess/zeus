module "snowflake" {
  source            = "../../modules/snowflake"
  organization_name = var.organization_name
  account_name      = var.account_name
  user              = var.user
  password          = var.password
  warehouse_name    = var.warehouse_name
  warehouse_size    = var.warehouse_size
}
