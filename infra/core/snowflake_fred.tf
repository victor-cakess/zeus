# FRED → Snowflake landing stack: the same shared snowflake_landing module as
# EIA/NOAA, with FRED's narrow (series, date, value) columns. Lands
# ZEUS_DEV.FRED.FRED_GRID + FRED_STAGE over curated/fred/, plus the
# ZEUS_DEV_FRED_LOADER key-pair user.
module "fred_landing" {
  source = "../modules/snowflake_landing"

  source_name    = "fred"
  project        = local.project
  env            = local.env
  prefix         = local.prefix
  bucket_name    = local.bucket_name
  bucket_arn     = aws_s3_bucket.data.arn
  account_id     = data.aws_caller_identity.current.account_id
  database_name  = snowflake_database.zeus_dev.name
  warehouse_name = snowflake_warehouse.this.name

  loader_public_key = var.fred_loader_public_key
  schema_comment    = "FRED energy price series (fuel spots, retail fuels, energy price indexes)."
  table_comment     = "Raw FRED price-series landing table — append-only, loaded by COPY INTO."

  # Mirrors src/lambdas/fred/ingest/schema.py (one row per series/date).
  columns = [
    { name = "SERIES", type = "VARCHAR(16777216)" },
    { name = "SERIES_ID", type = "VARCHAR(16777216)" },
    { name = "DATE", type = "DATE" },
    { name = "VALUE", type = "FLOAT" },
    { name = "INGESTION_DATE", type = "DATE" },
  ]
}
