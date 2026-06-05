# dbt transformer — the role + key-pair user that dbt authenticates as to build the
# staging→intermediate→marts models. Deliberately separate from the per-source LOADERs
# (which are USAGE + INSERT only): the transformer needs to READ the landing tables and
# CREATE its own schemas/objects, so it gets a broader-but-still-scoped grant set.
#
# Least-privilege: read-only on the EIA/NOAA landing schemas (USAGE + SELECT on the GRID
# tables), and CREATE SCHEMA on ZEUS_DEV so dbt owns the schemas it builds (STAGING, …)
# and every object within them. No INSERT/UPDATE on the landing tables — dbt never writes
# back to raw. Public key in tfvars (var.transformer_public_key); private key out-of-band.

resource "snowflake_account_role" "transformer" {
  name    = "ZEUS_DEV_TRANSFORMER_ROLE"
  comment = "dbt transformer: reads EIA/NOAA landing, builds + owns the modeled schemas."
}

resource "snowflake_service_user" "transformer" {
  name              = "ZEUS_DEV_TRANSFORMER"
  comment           = "dbt transformer (key-pair auth). Reads landing, builds staging→marts."
  rsa_public_key    = var.transformer_public_key
  default_role      = snowflake_account_role.transformer.name
  default_warehouse = snowflake_warehouse.this.name
}

resource "snowflake_grant_account_role" "transformer" {
  role_name = snowflake_account_role.transformer.name
  user_name = snowflake_service_user.transformer.name
}

# Roll the transformer role up to SYSADMIN. Objects dbt creates are owned by this role;
# without this grant they're orphaned — owned by a role no human inherits, so even
# ACCOUNTADMIN can't SELECT them. Granting it into the SYSADMIN hierarchy makes SYSADMIN
# (and ACCOUNTADMIN above it) inherit ownership of every current + future dbt object, so
# you can query the models interactively and admins retain control. Standard convention:
# all custom roles roll up to SYSADMIN.
resource "snowflake_grant_account_role" "transformer_to_sysadmin" {
  role_name        = snowflake_account_role.transformer.name
  parent_role_name = "SYSADMIN"
}

# --- Compute + database: run queries, and create the modeled schemas dbt manages ----

resource "snowflake_grant_privileges_to_account_role" "transformer_wh_usage" {
  account_role_name = snowflake_account_role.transformer.name
  privileges        = ["USAGE"]
  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.this.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "transformer_db" {
  account_role_name = snowflake_account_role.transformer.name
  privileges        = ["USAGE", "CREATE SCHEMA"]
  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.zeus_dev.name
  }
}

# --- Read-only on each source's landing schema (USAGE on schema + SELECT on GRID) -----
# Names come from the landing module outputs so they can't drift from what was created.

resource "snowflake_grant_privileges_to_account_role" "transformer_eia_schema_usage" {
  account_role_name = snowflake_account_role.transformer.name
  privileges        = ["USAGE"]
  on_schema {
    schema_name = "\"${snowflake_database.zeus_dev.name}\".\"${module.eia_landing.schema_name}\""
  }
}

resource "snowflake_grant_privileges_to_account_role" "transformer_eia_table_select" {
  account_role_name = snowflake_account_role.transformer.name
  privileges        = ["SELECT"]
  on_schema_object {
    object_type = "TABLE"
    object_name = "\"${snowflake_database.zeus_dev.name}\".\"${module.eia_landing.schema_name}\".\"${module.eia_landing.table_name}\""
  }
}

resource "snowflake_grant_privileges_to_account_role" "transformer_noaa_schema_usage" {
  account_role_name = snowflake_account_role.transformer.name
  privileges        = ["USAGE"]
  on_schema {
    schema_name = "\"${snowflake_database.zeus_dev.name}\".\"${module.noaa_landing.schema_name}\""
  }
}

resource "snowflake_grant_privileges_to_account_role" "transformer_noaa_table_select" {
  account_role_name = snowflake_account_role.transformer.name
  privileges        = ["SELECT"]
  on_schema_object {
    object_type = "TABLE"
    object_name = "\"${snowflake_database.zeus_dev.name}\".\"${module.noaa_landing.schema_name}\".\"${module.noaa_landing.table_name}\""
  }
}
