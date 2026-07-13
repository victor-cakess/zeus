# EIA interchange-data → Snowflake landing stack (storage integration + S3 trust,
# the ZEUS_DEV.EIA_INTERCHANGE.EIA_INTERCHANGE_GRID table + external stage, and a
# least-privilege key-pair loader the ingest Lambda authenticates as). Authored by
# the shared snowflake_landing module, same as EIA/EIA_REGION/NOAA/FRED.
#
# Third EIA-API source: hourly BA-to-BA power flows from the interchange-data
# route, signed (positive = fromba exports to toba). Both directions of every
# pair land — each BA reports its own view of the flow, and comparing them is
# the point (interchange-asymmetry test). Shares the EIA API key but owns its
# own loader, schema, and stage like any other source.
module "eia_interchange_landing" {
  source = "../modules/snowflake_landing"

  source_name    = "eia_interchange"
  project        = local.project
  env            = local.env
  prefix         = local.prefix
  bucket_name    = local.bucket_name
  bucket_arn     = aws_s3_bucket.data.arn
  account_id     = data.aws_caller_identity.current.account_id
  database_name  = snowflake_database.zeus_dev.name
  warehouse_name = snowflake_warehouse.this.name

  loader_public_key = var.eia_interchange_loader_public_key
  schema_comment    = "EIA hourly interchange data: BA-to-BA power flows, signed (positive = fromba exports to toba)."
  table_comment     = "Raw EIA interchange-data landing table — append-only, loaded by COPY INTO."

  # Mirrors src/lambdas/eia_interchange/ingest/schema.py.
  columns = [
    { name = "PERIOD", type = "TIMESTAMP_NTZ(9)" },
    { name = "FROMBA", type = "VARCHAR(16777216)" },
    { name = "FROMBA_NAME", type = "VARCHAR(16777216)" },
    { name = "TOBA", type = "VARCHAR(16777216)" },
    { name = "TOBA_NAME", type = "VARCHAR(16777216)" },
    { name = "VALUE", type = "FLOAT" },
    { name = "VALUE_UNITS", type = "VARCHAR(16777216)" },
    { name = "INGESTION_DATE", type = "DATE" },
  ]
}
