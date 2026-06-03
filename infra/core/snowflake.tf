# Shared Snowflake database. Each data source gets its own schema + landing stack
# within it, authored by the snowflake_landing module (snowflake_eia.tf / snowflake_noaa.tf).
resource "snowflake_database" "zeus_dev" {
  name    = "ZEUS_DEV"
  comment = "Energy platform — dev."
}
