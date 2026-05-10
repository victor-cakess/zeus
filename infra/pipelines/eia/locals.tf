locals {
  project = "zeus"
  env     = "dev"
  prefix  = "${local.project}-${local.env}"

  lambda_name  = "${local.prefix}-eia-extract"
  sfn_name     = "${local.prefix}-eia-daily"
  eb_rule_name = "${local.prefix}-eia-daily"
  ssm_key_path = "/${local.project}/${local.env}/eia/api_key"

  # Must mirror the naming convention in infra/core/locals.tf
  bucket_name = "${local.prefix}-energy-data"

  # Paths relative to this root (infra/pipelines/eia/)
  lambda_src_dir   = "${path.module}/../../../src/lambdas/eia"
  lambda_build_dir = "${path.module}/../../build/eia"

  balancing_authorities = [
    "EPE", "SRP", "MIDA", "NW", "AVRN", "NWMT", "PSCO", "TEN", "SW", "WALC",
    "JEA", "AECI", "TEPC", "NYIS", "MISO", "ISNE", "SWPP",
    "AVA", "GCPD", "TIDC", "SEPA", "PACE", "NY", "TVA", "PGE", "CAL",
    "CHPD", "FMPP", "DUK", "SC", "CPLE", "CISO", "TEC", "CAR",
    "FPC", "CENT", "NEVP", "FLA", "PJM", "PNM", "TAL", "TPWR",
    "LGEE", "SIKE", "NE", "TEX", "PSEI", "US48", "PACW", "AZPS", "FPL",
    "ERCO", "GVL", "SE", "GRID", "BANC", "HST", "BPAT", "GWA", "IID", "SOCO",
    "CPLW", "DEAA", "SEC", "SCL", "LDWP", "IPCO", "YAD", "DOPD", "MIDW",
    "SCEG",
  ]
}
