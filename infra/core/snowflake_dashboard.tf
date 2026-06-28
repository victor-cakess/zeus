# Governed read-only serving layer for the public Streamlit dashboard.
#
# The dashboard is internet-facing (Streamlit Community Cloud), so it gets its own
# least-privilege identity that can touch NOTHING but the REPORTING views: a leaf
# account role (deliberately NOT rolled up to SYSADMIN), a key-pair service user, a
# dedicated XS warehouse, and a 25-credit/month resource monitor so a runaway public
# dashboard can't burn the account. Terraform owns this whole shell + every grant; dbt
# owns the REPORTING views (transform/models/reporting/), building them into the schema
# shell created here. The user's private key lives in Streamlit secrets, NOT SSM —
# nothing secret is in this file or in state (only the public key, via tfvars).

# --- REPORTING schema shell (TF owns the shell; dbt builds the views into it) --------
# Owned by ACCOUNTADMIN. The transformer gets USAGE + CREATE VIEW so `dbt build` can
# `create or replace view` here; those views end up transformer-owned (→ SYSADMIN
# inherits). dbt's `create schema if not exists` is a no-op against this pre-created
# schema and needs no ownership.
resource "snowflake_schema" "reporting" {
  database = snowflake_database.zeus_dev.name
  name     = "REPORTING"
  comment  = "Public serving layer: read-only 1:1 views over the marts for the Streamlit dashboard. Shell owned by TF; views built by dbt."
}

resource "snowflake_grant_privileges_to_account_role" "transformer_reporting_create_view" {
  account_role_name = snowflake_account_role.transformer.name
  privileges        = ["USAGE", "CREATE VIEW"]
  on_schema {
    schema_name = "\"${snowflake_database.zeus_dev.name}\".\"${snowflake_schema.reporting.name}\""
  }
}

# --- Dashboard identity: leaf role (NO SYSADMIN rollup — least privilege) -------------
resource "snowflake_account_role" "dashboard" {
  name    = "ZEUS_DEV_DASHBOARD_ROLE"
  comment = "Public Streamlit dashboard: SELECT on REPORTING views only. Leaf role, deliberately not in the SYSADMIN hierarchy."
}

resource "snowflake_service_user" "dashboard" {
  name              = "ZEUS_DEV_DASHBOARD"
  comment           = "Public Streamlit dashboard (key-pair auth). Read-only on REPORTING views."
  rsa_public_key    = var.dashboard_public_key
  default_role      = snowflake_account_role.dashboard.name
  default_warehouse = snowflake_warehouse.dashboard.name
}

resource "snowflake_grant_account_role" "dashboard" {
  role_name = snowflake_account_role.dashboard.name
  user_name = snowflake_service_user.dashboard.name
}

# --- Dedicated XS warehouse (single cluster) + statement timeouts ---------------------
# Isolated from ZEUS_DEV_WH so the resource monitor's quota is meaningful and dashboard
# traffic can't slow dbt/ingest. Single cluster (no multi-cluster args) means a traffic
# spike QUEUES instead of scaling out — cost stays pinned at ~1 credit/hr while running.
# The 60s statement_timeout caps any single public query; the queued timeout keeps a
# burst of sessions from piling up.
resource "snowflake_warehouse" "dashboard" {
  name                                = "ZEUS_DEV_DASHBOARD_WH"
  warehouse_size                      = "x-small"
  auto_suspend                        = 60
  auto_resume                         = true
  resource_monitor                    = snowflake_resource_monitor.dashboard.name
  statement_timeout_in_seconds        = 60
  statement_queued_timeout_in_seconds = 30
}

# --- Resource monitor: 25 credits/month, notify 75%, suspend 100% ---------------------
# The hard bill ceiling for the public dashboard. notify_triggers is a list; the suspend
# triggers are single ints (provider 0.98). suspend_trigger lets in-flight statements
# finish; suspend_immediate_trigger hard-stops new ones — both at 100 so the dashboard
# can't overshoot the quota. No `warehouses` arg in 0.98: the warehouse attaches to this
# monitor from its side (resource_monitor above). notify_users points at the admin login
# (the service user has no email); the suspend trigger is the real guardrail regardless.
resource "snowflake_resource_monitor" "dashboard" {
  name         = "ZEUS_DEV_DASHBOARD_MONITOR"
  credit_quota = 25
  frequency    = "MONTHLY"
  # Snowflake rejects a start_timestamp in the past at CREATE, and frequency requires
  # one — so this anchors the monthly reset to a month boundary. Only validated on
  # create (unchanged value never re-diffs), so it can stay fixed; bump it to a future
  # date only if the monitor is ever destroyed/recreated.
  start_timestamp           = "2026-07-01 00:00"
  notify_triggers           = [75]
  suspend_trigger           = 100
  suspend_immediate_trigger = 100
  # No notify_users: it needs a Snowflake user *name* (the provider's `pokes7` is a
  # login name, which Snowflake rejects here), and a public-dashboard cost cap shouldn't
  # hinge on one person's notification prefs. The 75% NOTIFY still reaches account admins
  # who have notifications enabled; the 100% suspend is the actual guardrail.
}

# --- Grants to the dashboard role: compute + read-only on REPORTING -------------------
resource "snowflake_grant_privileges_to_account_role" "dashboard_wh_usage" {
  account_role_name = snowflake_account_role.dashboard.name
  privileges        = ["USAGE"]
  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.dashboard.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "dashboard_db_usage" {
  account_role_name = snowflake_account_role.dashboard.name
  privileges        = ["USAGE"]
  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.zeus_dev.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "dashboard_schema_usage" {
  account_role_name = snowflake_account_role.dashboard.name
  privileges        = ["USAGE"]
  on_schema {
    schema_name = "\"${snowflake_database.zeus_dev.name}\".\"${snowflake_schema.reporting.name}\""
  }
}

# SELECT on ALL existing views in REPORTING (covers anything already built on a re-apply).
resource "snowflake_grant_privileges_to_account_role" "dashboard_select_all_views" {
  account_role_name = snowflake_account_role.dashboard.name
  privileges        = ["SELECT"]
  on_schema_object {
    all {
      object_type_plural = "VIEWS"
      in_schema          = "\"${snowflake_database.zeus_dev.name}\".\"${snowflake_schema.reporting.name}\""
    }
  }
}

# SELECT on FUTURE views in REPORTING — so dbt-created views (built AFTER this apply, and
# any new vw_* added later) are authorized automatically, with no second terraform apply.
resource "snowflake_grant_privileges_to_account_role" "dashboard_select_future_views" {
  account_role_name = snowflake_account_role.dashboard.name
  privileges        = ["SELECT"]
  on_schema_object {
    future {
      object_type_plural = "VIEWS"
      in_schema          = "\"${snowflake_database.zeus_dev.name}\".\"${snowflake_schema.reporting.name}\""
    }
  }
}
