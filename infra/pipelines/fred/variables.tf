# 150 (not 7 like EIA/NOAA): FRED mixes daily, weekly, and monthly series, and the
# BLS revises the monthly PPI/CPI indexes up to ~4 months after first release. A
# 150-day window keeps every series represented in every run and re-captures the
# full revision cycle; the overlap is deduped downstream like everywhere else.
variable "lookback_days" {
  type    = number
  default = 150
}
