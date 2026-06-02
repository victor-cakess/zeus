# EIA → Snowflake glue: storage integration + S3 trust, the ZEUS_DEV.EIA.EIA_GRID
# landing table and its external stage, and a least-privilege key-pair loader the
# ingest Lambda authenticates as. The Lambda runs `COPY INTO EIA_GRID` from the
# stage; Snowflake reads the curated Parquet from S3 itself via the integration.

locals {
  curated_eia_location = "s3://${local.bucket_name}/curated/eia/"
  snowflake_eia_role   = "${local.prefix}-snowflake-eia"
}

# --- Storage integration ↔ AWS IAM role handshake (single apply) ------------
# The integration is told the role ARN up front (deterministic from account id +
# fixed name); the role's trust policy is then built from the integration's
# computed IAM user + external id. One direction, no cycle.

resource "snowflake_storage_integration" "eia" {
  name                      = "ZEUS_DEV_EIA_S3_INT"
  type                      = "EXTERNAL_STAGE"
  storage_provider          = "S3"
  enabled                   = true
  storage_allowed_locations = [local.curated_eia_location]
  storage_aws_role_arn      = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.snowflake_eia_role}"
}

resource "aws_iam_role" "snowflake_eia" {
  name = local.snowflake_eia_role

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = snowflake_storage_integration.eia.storage_aws_iam_user_arn }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "sts:ExternalId" = snowflake_storage_integration.eia.storage_aws_external_id
        }
      }
    }]
  })

  tags = {
    Project     = local.project
    Environment = local.env
    ManagedBy   = "terraform"
  }
}

resource "aws_iam_role_policy" "snowflake_eia" {
  name = "s3-read-curated-eia"
  role = aws_iam_role.snowflake_eia.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = "${aws_s3_bucket.data.arn}/curated/eia/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = aws_s3_bucket.data.arn
        Condition = {
          StringLike = { "s3:prefix" = ["curated/eia/*"] }
        }
      },
    ]
  })
}

# --- Database / schema / landing table / stage -----------------------------

resource "snowflake_database" "zeus_dev" {
  name    = "ZEUS_DEV"
  comment = "Energy platform — dev."
}

resource "snowflake_schema" "eia" {
  database = snowflake_database.zeus_dev.name
  name     = "EIA"
  comment  = "EIA hourly fuel-type data."
}

# Append-only landing table. Columns mirror src/lambdas/eia/ingest/schema.py.
# Dups from the 7-day lookback overlap are deduped downstream in dbt on
# (period, respondent, fueltype), keeping the latest ingestion_date.
resource "snowflake_table" "eia_grid" {
  database = snowflake_database.zeus_dev.name
  schema   = snowflake_schema.eia.name
  name     = "EIA_GRID"
  comment  = "Raw EIA grid mix landing table — append-only, loaded by COPY INTO."

  column {
    name = "PERIOD"
    type = "TIMESTAMP_NTZ(9)"
  }
  column {
    name = "RESPONDENT"
    type = "VARCHAR(16777216)"
  }
  column {
    name = "RESPONDENT_NAME"
    type = "VARCHAR(16777216)"
  }
  column {
    name = "FUELTYPE"
    type = "VARCHAR(16777216)"
  }
  column {
    name = "TYPE_NAME"
    type = "VARCHAR(16777216)"
  }
  column {
    name = "VALUE"
    type = "FLOAT"
  }
  column {
    name = "VALUE_UNITS"
    type = "VARCHAR(16777216)"
  }
  column {
    name = "INGESTION_DATE"
    type = "DATE"
  }
}

resource "snowflake_stage" "eia" {
  database            = snowflake_database.zeus_dev.name
  schema              = snowflake_schema.eia.name
  name                = "EIA_STAGE"
  url                 = local.curated_eia_location
  storage_integration = snowflake_storage_integration.eia.name
  file_format         = "TYPE = PARQUET USE_LOGICAL_TYPE = TRUE"
  comment             = "External stage over curated/eia/ for COPY INTO EIA_GRID."
}

# --- Least-privilege key-pair loader (the Lambda authenticates as this) -----

resource "snowflake_account_role" "eia_loader" {
  name    = "ZEUS_DEV_EIA_LOADER_ROLE"
  comment = "USAGE + INSERT only, for the EIA ingest Lambda's COPY INTO."
}

resource "snowflake_service_user" "eia_loader" {
  name              = "ZEUS_DEV_EIA_LOADER"
  comment           = "Programmatic loader for the EIA ingest Lambda (key-pair auth)."
  rsa_public_key    = var.eia_loader_public_key
  default_role      = snowflake_account_role.eia_loader.name
  default_warehouse = snowflake_warehouse.this.name
}

resource "snowflake_grant_account_role" "eia_loader" {
  role_name = snowflake_account_role.eia_loader.name
  user_name = snowflake_service_user.eia_loader.name
}

resource "snowflake_grant_privileges_to_account_role" "wh_usage" {
  account_role_name = snowflake_account_role.eia_loader.name
  privileges        = ["USAGE"]
  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.this.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "db_usage" {
  account_role_name = snowflake_account_role.eia_loader.name
  privileges        = ["USAGE"]
  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.zeus_dev.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "schema_usage" {
  account_role_name = snowflake_account_role.eia_loader.name
  privileges        = ["USAGE"]
  on_schema {
    schema_name = "\"${snowflake_database.zeus_dev.name}\".\"${snowflake_schema.eia.name}\""
  }
}

resource "snowflake_grant_privileges_to_account_role" "table_insert" {
  account_role_name = snowflake_account_role.eia_loader.name
  privileges        = ["INSERT"]
  on_schema_object {
    object_type = "TABLE"
    object_name = "\"${snowflake_database.zeus_dev.name}\".\"${snowflake_schema.eia.name}\".\"${snowflake_table.eia_grid.name}\""
  }
}

resource "snowflake_grant_privileges_to_account_role" "stage_usage" {
  account_role_name = snowflake_account_role.eia_loader.name
  privileges        = ["USAGE"]
  on_schema_object {
    object_type = "STAGE"
    object_name = "\"${snowflake_database.zeus_dev.name}\".\"${snowflake_schema.eia.name}\".\"${snowflake_stage.eia.name}\""
  }
}
