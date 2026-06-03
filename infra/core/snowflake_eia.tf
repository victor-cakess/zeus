# EIA → Snowflake landing stack (storage integration + S3 trust, the
# ZEUS_DEV.EIA.EIA_GRID table + external stage, and a least-privilege key-pair
# loader the ingest Lambda authenticates as). Authored by the shared
# snowflake_landing module; NOAA uses the same module in snowflake_noaa.tf.
#
# NOTE: the resources below were previously inline here. Moving them into the
# module changes their state addresses (e.g. snowflake_table.eia_grid ->
# module.eia_landing.snowflake_table.this) — see the `terraform state mv` block in
# the README before first apply. The module reproduces every name/comment exactly,
# so the post-move plan is "No changes".
module "eia_landing" {
  source = "../modules/snowflake_landing"

  source_name    = "eia"
  project        = local.project
  env            = local.env
  prefix         = local.prefix
  bucket_name    = local.bucket_name
  bucket_arn     = aws_s3_bucket.data.arn
  account_id     = data.aws_caller_identity.current.account_id
  database_name  = snowflake_database.zeus_dev.name
  warehouse_name = snowflake_warehouse.this.name

  loader_public_key = var.eia_loader_public_key
  schema_comment    = "EIA hourly fuel-type data."
  table_comment     = "Raw EIA grid mix landing table — append-only, loaded by COPY INTO."

  # Mirrors src/lambdas/eia/ingest/schema.py.
  columns = [
    { name = "PERIOD", type = "TIMESTAMP_NTZ(9)" },
    { name = "RESPONDENT", type = "VARCHAR(16777216)" },
    { name = "RESPONDENT_NAME", type = "VARCHAR(16777216)" },
    { name = "FUELTYPE", type = "VARCHAR(16777216)" },
    { name = "TYPE_NAME", type = "VARCHAR(16777216)" },
    { name = "VALUE", type = "FLOAT" },
    { name = "VALUE_UNITS", type = "VARCHAR(16777216)" },
    { name = "INGESTION_DATE", type = "DATE" },
  ]
}
