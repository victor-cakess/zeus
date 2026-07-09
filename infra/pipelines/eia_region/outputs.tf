output "ingest_function_name" {
  value = module.ingest.function_name
}

output "ingest_function_arn" {
  value = module.ingest.function_arn
}

# Single source of truth for THIS pipeline's BA list — the orchestration root reads
# it to build the state machine's invoke payload. Deliberately duplicated from the
# eia root (self-contained roots): the fuel-type list pruned 10 permanently
# generation-empty BAs, a judgment that may diverge for demand data.
output "balancing_authorities" {
  value = local.balancing_authorities
}
