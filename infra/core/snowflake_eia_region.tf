# EIA region-data → Snowflake landing stack (storage integration + S3 trust, the
# ZEUS_DEV.EIA_REGION.EIA_REGION_GRID table + external stage, and a least-privilege
# key-pair loader the ingest Lambda authenticates as). Authored by the shared
# snowflake_landing module, same as EIA/NOAA/FRED.
#
# Second EIA-API source: `eia` means the fuel-type dataset (named before it had
# siblings); this is the hourly Demand / Day-ahead forecast / Net generation /
# Total interchange dataset from the same API. It shares the EIA API key but owns
# its own loader, schema, and stage like any other source.
module "eia_region_landing" {
  source = "../modules/snowflake_landing"

  source_name    = "eia_region"
  project        = local.project
  env            = local.env
  prefix         = local.prefix
  bucket_name    = local.bucket_name
  bucket_arn     = aws_s3_bucket.data.arn
  account_id     = data.aws_caller_identity.current.account_id
  database_name  = snowflake_database.zeus_dev.name
  warehouse_name = snowflake_warehouse.this.name

  loader_public_key = var.eia_region_loader_public_key
  schema_comment    = "EIA hourly region data: demand (D), day-ahead demand forecast (DF), net generation (NG), total interchange (TI)."
  table_comment     = "Raw EIA region-data landing table — append-only, loaded by COPY INTO."

  # Mirrors src/lambdas/eia_region/ingest/schema.py.
  columns = [
    { name = "PERIOD", type = "TIMESTAMP_NTZ(9)" },
    { name = "RESPONDENT", type = "VARCHAR(16777216)" },
    { name = "RESPONDENT_NAME", type = "VARCHAR(16777216)" },
    { name = "TYPE", type = "VARCHAR(16777216)" },
    { name = "TYPE_NAME", type = "VARCHAR(16777216)" },
    { name = "VALUE", type = "FLOAT" },
    { name = "VALUE_UNITS", type = "VARCHAR(16777216)" },
    { name = "INGESTION_DATE", type = "DATE" },
  ]
}
