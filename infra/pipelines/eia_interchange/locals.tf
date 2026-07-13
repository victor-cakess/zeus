locals {
  project = "zeus"
  env     = "dev"

  # The eia/eia_region 71-BA list PLUS the 10 fromba codes that list pruned from
  # a *generation* perspective (AEC … WWA) — they report real interchange, and
  # without their own fan-out their exports are invisible (we draw each BA's own
  # positive reports). Full fromba facet coverage = 81 units.
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
    "AEC", "EEI", "GLHB", "GRIF", "HGMA", "NSB", "SPA", "WACM", "WAUW", "WWA",
  ]
}
