{#
  Use the model's +schema verbatim as the schema name (STAGING, INTERMEDIATE, MARTS…)
  instead of dbt's default "<target_schema>_<custom>". Cleaner schema names for the
  downstream consumers (BI, ML, Cortex). Models with no +schema fall back to the
  profile's target schema.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema | trim }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
