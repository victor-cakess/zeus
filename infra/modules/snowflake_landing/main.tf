# One data source's Snowflake landing stack: storage integration + S3 trust, the
# <SOURCE>_GRID table and its external stage over curated/<source>/, and a
# least-privilege key-pair loader the ingest Lambda authenticates as. Every name is
# derived from source_name so each source is one module call (see infra/core).

locals {
  up = upper(var.source_name)               # EIA / NOAA
  sf = upper(replace(var.prefix, "-", "_")) # zeus-dev -> ZEUS_DEV

  role_name        = "${var.prefix}-snowflake-${var.source_name}" # zeus-dev-snowflake-eia
  integration_name = "${local.sf}_${local.up}_S3_INT"             # ZEUS_DEV_EIA_S3_INT
  schema_name      = local.up                                     # EIA
  table_name       = "${local.up}_GRID"                           # EIA_GRID
  stage_name       = "${local.up}_STAGE"                          # EIA_STAGE
  loader_role_name = "${local.sf}_${local.up}_LOADER_ROLE"        # ZEUS_DEV_EIA_LOADER_ROLE
  loader_user_name = "${local.sf}_${local.up}_LOADER"             # ZEUS_DEV_EIA_LOADER
  curated_location = "s3://${var.bucket_name}/curated/${var.source_name}/"
}

# --- Storage integration ↔ AWS IAM role handshake (single apply) ------------
# The integration is told the role ARN up front (deterministic from account id +
# fixed name); the role's trust policy is then built from the integration's
# computed IAM user + external id. One direction, no cycle.

resource "snowflake_storage_integration" "this" {
  name                      = local.integration_name
  type                      = "EXTERNAL_STAGE"
  storage_provider          = "S3"
  enabled                   = true
  storage_allowed_locations = [local.curated_location]
  storage_aws_role_arn      = "arn:aws:iam::${var.account_id}:role/${local.role_name}"
}

resource "aws_iam_role" "this" {
  name = local.role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = snowflake_storage_integration.this.storage_aws_iam_user_arn }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "sts:ExternalId" = snowflake_storage_integration.this.storage_aws_external_id
        }
      }
    }]
  })

  tags = {
    Project     = var.project
    Environment = var.env
    ManagedBy   = "terraform"
  }
}

resource "aws_iam_role_policy" "this" {
  name = "s3-read-curated-${var.source_name}"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = "${var.bucket_arn}/curated/${var.source_name}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = var.bucket_arn
        Condition = {
          StringLike = { "s3:prefix" = ["curated/${var.source_name}/*"] }
        }
      },
    ]
  })
}

# --- Schema / landing table / stage ----------------------------------------

resource "snowflake_schema" "this" {
  database = var.database_name
  name     = local.schema_name
  comment  = var.schema_comment
}

# Append-only landing table. Columns mirror the source's pyarrow schema. Dups from
# the lookback overlap are deduped downstream in dbt, keeping the latest ingestion.
resource "snowflake_table" "this" {
  database = var.database_name
  schema   = snowflake_schema.this.name
  name     = local.table_name
  comment  = var.table_comment

  dynamic "column" {
    for_each = var.columns
    content {
      name = column.value.name
      type = column.value.type
    }
  }
}

resource "snowflake_stage" "this" {
  database            = var.database_name
  schema              = snowflake_schema.this.name
  name                = local.stage_name
  url                 = local.curated_location
  storage_integration = snowflake_storage_integration.this.name
  file_format         = "TYPE = PARQUET USE_LOGICAL_TYPE = TRUE"
  comment             = "External stage over curated/${var.source_name}/ for COPY INTO ${local.table_name}."
}

# --- Least-privilege key-pair loader (the Lambda authenticates as this) -----

resource "snowflake_account_role" "this" {
  name    = local.loader_role_name
  comment = "USAGE + INSERT only, for the ${local.up} ingest Lambda's COPY INTO."
}

resource "snowflake_service_user" "this" {
  name              = local.loader_user_name
  comment           = "Programmatic loader for the ${local.up} ingest Lambda (key-pair auth)."
  rsa_public_key    = var.loader_public_key
  default_role      = snowflake_account_role.this.name
  default_warehouse = var.warehouse_name
}

resource "snowflake_grant_account_role" "this" {
  role_name = snowflake_account_role.this.name
  user_name = snowflake_service_user.this.name
}

resource "snowflake_grant_privileges_to_account_role" "wh_usage" {
  account_role_name = snowflake_account_role.this.name
  privileges        = ["USAGE"]
  on_account_object {
    object_type = "WAREHOUSE"
    object_name = var.warehouse_name
  }
}

resource "snowflake_grant_privileges_to_account_role" "db_usage" {
  account_role_name = snowflake_account_role.this.name
  privileges        = ["USAGE"]
  on_account_object {
    object_type = "DATABASE"
    object_name = var.database_name
  }
}

resource "snowflake_grant_privileges_to_account_role" "schema_usage" {
  account_role_name = snowflake_account_role.this.name
  privileges        = ["USAGE"]
  on_schema {
    schema_name = "\"${var.database_name}\".\"${snowflake_schema.this.name}\""
  }
}

resource "snowflake_grant_privileges_to_account_role" "table_insert" {
  account_role_name = snowflake_account_role.this.name
  privileges        = ["INSERT"]
  on_schema_object {
    object_type = "TABLE"
    object_name = "\"${var.database_name}\".\"${snowflake_schema.this.name}\".\"${snowflake_table.this.name}\""
  }
}

resource "snowflake_grant_privileges_to_account_role" "stage_usage" {
  account_role_name = snowflake_account_role.this.name
  privileges        = ["USAGE"]
  on_schema_object {
    object_type = "STAGE"
    object_name = "\"${var.database_name}\".\"${snowflake_schema.this.name}\".\"${snowflake_stage.this.name}\""
  }
}
