module "pipeline" {
  source = "../../modules/pipeline"

  source_name = "eia"
  prefix      = "${local.project}-${local.env}"
  project     = local.project
  env         = local.env

  bucket_name      = data.terraform_remote_state.core.outputs.bucket_name
  bucket_arn       = data.terraform_remote_state.core.outputs.bucket_arn
  alerts_topic_arn = data.terraform_remote_state.core.outputs.alerts_topic_arn

  schedule_cron   = var.schedule_cron
  lookback_days   = var.lookback_days
  fanout_items    = local.balancing_authorities
  max_concurrency = 20

  extract_src_dir   = "${path.module}/../../../src/lambdas/eia/extract"
  transform_src_dir = "${path.module}/../../../src/lambdas/eia/transform"
  shared_src_dir    = "${path.module}/../../../src/shared"
  build_root        = "${path.module}/../../build"
}
