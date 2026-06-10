# CI clone runner — the role + key-pair user GitHub Actions authenticates as to run
# `dbt build` on PRs against a zero-copy clone of ZEUS_DEV (phase 2 of the CI plan;
# workflow in .github/workflows/dbt-clone-ci.yml, clone macros in transform/macros/ci/).
#
# Privilege model: CREATE DATABASE on the account (to create the clone), USAGE on
# ZEUS_DEV (required to clone it), USAGE on the shared warehouse to run the build —
# plus the TRANSFORMER role granted into this one. The inheritance is what makes the
# clone buildable: cloning a database copies each CHILD object's grants/ownership
# from the source (the clone owner owns only the database shell), so inside
# ZEUS_CI_PR_<n> the modeled schemas are still owned by ZEUS_DEV_TRANSFORMER_ROLE
# and the landing tables still SELECT-grant to it. Without the inheritance the CI
# role can clone but touch nothing inside (verified empirically: "Schema 'STAGING'
# already exists, but current role has no privileges on it").
#
# Accepted caveat: via the inherited transformer role this key can also read the
# landing tables and rebuild/drop the modeled schemas in ZEUS_DEV itself (all
# dbt-rebuildable; no landing writes). Separate user + key so CI stays individually
# auditable and revocable. Fine for a dev database.

resource "snowflake_account_role" "ci" {
  name    = "ZEUS_DEV_CI_ROLE"
  comment = "CI clone runner: clones ZEUS_DEV per PR, dbt-builds the clone, drops it."
}

resource "snowflake_service_user" "ci" {
  name              = "ZEUS_DEV_CI"
  comment           = "GitHub Actions dbt clone CI (key-pair auth). Clones ZEUS_DEV, builds, drops."
  rsa_public_key    = var.ci_public_key
  default_role      = snowflake_account_role.ci.name
  default_warehouse = snowflake_warehouse.this.name
}

resource "snowflake_grant_account_role" "ci" {
  role_name = snowflake_account_role.ci.name
  user_name = snowflake_service_user.ci.name
}

# Roll the CI role up to SYSADMIN, same rationale as the transformer: the per-PR
# clone databases are owned by this role, so without the rollup a clone leaked by a
# dead runner would be an orphan not even ACCOUNTADMIN could drop.
resource "snowflake_grant_account_role" "ci_to_sysadmin" {
  role_name        = snowflake_account_role.ci.name
  parent_role_name = "SYSADMIN"
}

# Transformer → CI: the CI role inherits the transformer's privileges, which the
# clone's child objects carry copies of (see header). This is what lets dbt build
# read the cloned landing tables and write the cloned modeled schemas.
resource "snowflake_grant_account_role" "transformer_to_ci" {
  role_name        = snowflake_account_role.transformer.name
  parent_role_name = snowflake_account_role.ci.name
}

resource "snowflake_grant_privileges_to_account_role" "ci_create_database" {
  account_role_name = snowflake_account_role.ci.name
  privileges        = ["CREATE DATABASE"]
  on_account        = true
}

resource "snowflake_grant_privileges_to_account_role" "ci_wh_usage" {
  account_role_name = snowflake_account_role.ci.name
  privileges        = ["USAGE"]
  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.this.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "ci_db_usage" {
  account_role_name = snowflake_account_role.ci.name
  privileges        = ["USAGE"]
  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.zeus_dev.name
  }
}
