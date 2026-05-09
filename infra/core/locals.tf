locals {
  project     = "zeus"
  env         = "dev"
  prefix      = "${local.project}-${local.env}"
  bucket_name = "${local.prefix}-energy-data"
}
