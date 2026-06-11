locals {
  project = "zeus"
  env     = "dev"

  # FRED price series — each unit maps to its FRED series id in the client's
  # SERIES map (src/lambdas/fred/ingest/client.py — keep in sync). National-level
  # prices: no `ba` key; they join to grid data downstream on date.
  series = [
    "WTI", "BRENT", "HENRYHUB", "HEATINGOIL", "PROPANEMT", "JETFUEL", "GASNYH", "GASGULF",
    "GASOLINE", "DIESEL",
    "COALPPI", "NATGASPPI", "ELECPPI", "ELECPRICE", "CPIENERGY",
  ]
}
