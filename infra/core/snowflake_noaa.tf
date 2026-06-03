# NOAA → Snowflake landing stack: the same shared snowflake_landing module as EIA,
# with NOAA's wide daily-weather columns. Lands ZEUS_DEV.NOAA.NOAA_GRID + NOAA_STAGE
# over curated/noaa/, plus the ZEUS_DEV_NOAA_LOADER key-pair user.
module "noaa_landing" {
  source = "../modules/snowflake_landing"

  source_name    = "noaa"
  project        = local.project
  env            = local.env
  prefix         = local.prefix
  bucket_name    = local.bucket_name
  bucket_arn     = aws_s3_bucket.data.arn
  account_id     = data.aws_caller_identity.current.account_id
  database_name  = snowflake_database.zeus_dev.name
  warehouse_name = snowflake_warehouse.this.name

  loader_public_key = var.noaa_loader_public_key
  schema_comment    = "NOAA daily weather summaries."
  table_comment     = "Raw NOAA daily weather landing table — append-only, loaded by COPY INTO."

  # Mirrors src/lambdas/noaa/ingest/schema.py (one row per ba/station/date, wide).
  columns = [
    { name = "DATE", type = "DATE" },
    { name = "STATION", type = "VARCHAR(16777216)" },
    { name = "BA", type = "VARCHAR(16777216)" },
    { name = "TMAX", type = "FLOAT" },
    { name = "TMIN", type = "FLOAT" },
    { name = "TAVG", type = "FLOAT" },
    { name = "PRCP", type = "FLOAT" },
    { name = "SNOW", type = "FLOAT" },
    { name = "SNWD", type = "FLOAT" },
    { name = "AWND", type = "FLOAT" },
    { name = "WSF2", type = "FLOAT" },
    { name = "WSF5", type = "FLOAT" },
    { name = "WDF2", type = "FLOAT" },
    { name = "RHAV", type = "FLOAT" },
    { name = "ASLP", type = "FLOAT" },
    { name = "ADPT", type = "FLOAT" },
    { name = "INGESTION_DATE", type = "DATE" },
  ]
}
