output "ingest_function_name" {
  value = module.ingest.function_name
}

output "ingest_function_arn" {
  value = module.ingest.function_arn
}

# Single source of truth for THIS pipeline's fromba fan-out list — the orchestration
# root reads it to build the state machine's invoke payload. Deliberately duplicated
# from the eia root (self-contained roots): which BAs report interchange is its own
# judgment, pruned against this route's actual coverage.
output "balancing_authorities" {
  value = local.balancing_authorities
}
