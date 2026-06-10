{#
  CI clone lifecycle — run-operation macros for .github/workflows/dbt-clone-ci.yml,
  so the workflow needs no SQL client beyond dbt itself. Runs as ZEUS_DEV_CI
  (infra/core/snowflake_ci.tf), connected to ZEUS_DEV; only the build step targets
  the clone (SNOWFLAKE_DATABASE=ZEUS_CI_PR_<n>).

  Safety contract: the ZEUS_CI_PR_ prefix is hardcoded here and the suffix must be
  digits-only, so these macros physically cannot create or drop anything outside
  the CI namespace, whatever the workflow passes. CREATE OR REPLACE makes re-runs
  and leaked clones from failed cleanups self-heal on the next run.
#}

{% macro _ci_clone_name(suffix) %}
    {%- set s = suffix | string | trim -%}
    {%- if not modules.re.match('^[0-9]+$', s) -%}
        {{ exceptions.raise_compiler_error("CI clone suffix must be digits only (a PR number), got: '" ~ s ~ "'") }}
    {%- endif -%}
    {{- "ZEUS_CI_PR_" ~ s -}}
{% endmacro %}

{% macro ci_clone_create(suffix) %}
    {%- set db = _ci_clone_name(suffix) -%}
    {% do log("Creating clone " ~ db ~ " from ZEUS_DEV", info=True) %}
    {% do run_query("create or replace database " ~ db ~ " clone ZEUS_DEV") %}
    {% do log("Clone " ~ db ~ " ready", info=True) %}
{% endmacro %}

{% macro ci_clone_drop(suffix) %}
    {%- set db = _ci_clone_name(suffix) -%}
    {% do log("Dropping clone " ~ db, info=True) %}
    {% do run_query("drop database if exists " ~ db) %}
    {% do log("Clone " ~ db ~ " dropped", info=True) %}
{% endmacro %}
