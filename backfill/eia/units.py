"""Balancing authorities to backfill.

Source of truth is infra/pipelines/eia/locals.tf (`balancing_authorities`).
Keep this list in sync if BAs are added/removed there.
"""

BALANCING_AUTHORITIES = [
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
