locals {
  project = "zeus"
  env     = "dev"

  # Balancing authorities — each fans out to its station list in the NOAA client.
  # BA codes match EIA's so weather joins to grid data on `ba` downstream.
  balancing_authorities = ["CISO", "PJM", "ERCO", "MISO", "ISNE", "NYIS", "SWPP", "TVA", "SOCO", "DUK", "FPL", "BPAT", "PSCO", "SRP"]
}
