# Architectural Decisions Records

Design decisions for the Zeus data platform — a multi-source energy data ingestion and modeling system. Cross-cutting decisions (region, Terraform structure, S3 layout, secrets, packaging, observability) are at the top of this document. Pipeline-specific decisions are grouped under their own sections below.

Format per entry: the decision, alternatives considered, why the chosen option won, and known trade-offs.

---

# Cross-cutting decisions

## 1. AWS region: `sa-east-1` (São Paulo)

**Chosen:** `sa-east-1`.

**Alternatives:**
- `us-west-2` (Oregon) — co-locate with Snowflake account.
- `us-east-1` (N. Virginia) — cheapest AWS region.

**Why:**
- Matches the pre-existing Terraform state backend (`zeus-analytics-tfstate` is in `sa-east-1`). Splitting infra across regions adds operational complexity.
- Local development is in Brazil; lower latency for CLI-driven tests and console use.
- Snowflake (Oregon) reads cross-region from S3 fine — a Snowflake storage integration handles it transparently.

**Trade-offs:**
- Snowflake-side reads cross paid AWS data-transfer ($0.02/GB out of `sa-east-1`). At current volumes, cost is negligible.
- Slight read latency for Snowflake (cross-region), invisible at our row volumes.

---

## 2. Terraform structure: decoupled multi-state roots

**Chosen:** Two independent Terraform state roots. Long-lived shared infrastructure in `infra/core/`; each pipeline is its own root under `infra/pipelines/<source>/`.

```
infra/
  core/
    backend.tf, providers.tf, variables.tf, locals.tf, main.tf, outputs.tf, sns.tf
  pipelines/
    eia/
      backend.tf, providers.tf, variables.tf, locals.tf
      data.tf, ssm.tf, lambda.tf, step_function.tf, eventbridge.tf, sns.tf, outputs.tf
    noaa/    # future — zero changes to core
    fred/    # future
    epa/     # future
  build/     # gitignored Lambda zip artifacts
```

Each pipeline discovers shared resources via data sources and `terraform_remote_state`:
- Shared S3 bucket via `data "aws_s3_bucket"` (name derived from `${prefix}-energy-data` convention).
- Shared SNS alerts topic via `terraform_remote_state` against `infra/core` state.

**Naming convention:** `${project}-${env}-<source>-<resource>` (e.g. `zeus-dev-eia-extract`). SSM paths: `/${project}/${env}/<source>/api_key`. Defined via locals in each root.

**Alternatives considered:**
- **Single nested-module hierarchy** (`environments/dev → modules/aws → modules/aws/sources/eia`). Every new source required editing `modules/aws/main.tf` and `modules/aws/outputs.tf`; outputs bubbled through two module layers; one bad apply could affect all shared and pipeline resources in the same plan.
- **Per-resource-type submodules** (`modules/aws/lambdas/`, `modules/aws/step_functions/`). Splits a single pipeline across multiple folders; ownership harder to follow.

**Why decoupled roots won:**
- **Blast radius.** A broken pipeline apply cannot touch the S3 bucket, Snowflake warehouse, or SNS topic — they are in separate state.
- **True isolation.** Each pipeline is planned and applied independently.
- **Zero-touch onboarding.** New source = new directory under `infra/pipelines/`. `infra/core/` is never modified for a new pipeline.
- **No output bubbling.** Outputs live exactly where the resource lives.

**Trade-offs:**
- `project` and `env` locals are duplicated across roots. Acceptable at current scale; a shared variable file or `terraform_remote_state` lookup becomes worthwhile around 5+ pipeline roots.
- The `null_resource` Lambda build pattern is copy-pasted per pipeline. A shared module eliminates this at ~3+ pipelines.

---

## 3. S3 layout: two-layer architecture, source-first prefix, multi-level Hive partitioning

**Chosen:** Single shared bucket `${prefix}-energy-data` with two layers:
- **Raw layer** (`raw/<source>/`): one JSON file per atomic unit, immutable, append-only.
- **Curated layer** (`curated/<source>/`): one Snappy-compressed Parquet per day, produced by the transform stage after the fan-out completes.

```
s3://zeus-dev-energy-data/raw/<source>/ingestion_year=YYYY/ingestion_month=MM/ingestion_day=DD/<unit>.json
s3://zeus-dev-energy-data/curated/<source>/ingestion_year=YYYY/ingestion_month=MM/ingestion_day=DD/<source>_grid.parquet
```

**Why:**
- **Source-first prefix** so each source gets its own Snowflake stage, Snowpipe, IAM scope, and lifecycle policy without cross-source coupling.
- **Multi-level Hive partitioning on date** keeps S3-console browsability healthy as the bucket grows. Flat date folders would become 1,825 sibling entries per source over 5 years; multi-level keeps every depth ≤ 30.
- **No `<unit>=` partition.** The unit is in the JSON payload and in the filename. The path partition bought no query pruning (Snowpipe → raw table, not external table) and just added a directory level.
- **Filename = `<unit>.json`** (raw) / `<source>_grid.parquet` (curated) keeps per-unit traceability for debugging without polluting the path.
- **Two-layer separation** keeps raw JSON as the immutable source of truth; the curated Parquet is a derived, typed artifact. Re-running the transform never touches raw.
- **Append-only, immutable.** Downstream de-dup happens at query time in Snowflake (`qualify row_number() over (...) = 1`), not at write time.

**Alternatives considered:**
- **Date-first prefix** (`raw/ingestion_date=.../<source>/...`) — better for "what did we ingest on day X" cross-source listings but worse for per-source IAM, lifecycle, and Snowpipe scoping.
- **Flat `ingestion_date=YYYY-MM-DD/`** — fewer levels but degraded console browsability over time.
- **Single layer (raw JSON only, transform in Snowflake)** — skip the curated Parquet; Snowpipe loads raw JSON, dbt handles typing. Rejected: a typed Parquet external stage is cheaper and faster to query; also keeps S3 → Snowflake coupling simpler.

**Trade-offs:**
- Downstream needs to know to de-dup. Owned by the dbt staging model.
- Curated layer is a derived artifact — if the transform schema changes, old curated partitions are not backfilled automatically.

---

## 4. Source code layout: `src/lambdas/<name>/`

**Chosen:** `src/lambdas/<lambda-name>/handler.py` + `requirements.txt`.

**Why:**
- `src/` is the conventional Python project root (visible to type checkers, IDE indexers, and packaging tools).
- Reusable: `src/lambdas/eia/`, `src/lambdas/noaa/`, `src/lambdas/fred/`, `src/lambdas/epa/` follow the same pattern.
- Keeps Lambda source out of `extraction/` (which is gitignored — a source-of-truth lambda must be in git).

---

## 5. Secret store: SSM Parameter Store (SecureString)

**Chosen:** SSM Parameter Store, `SecureString`, AWS-managed KMS key (`alias/aws/ssm`).

**Alternatives:**
- **AWS Secrets Manager** — supports automatic rotation, $0.40/secret/month + $0.05/10k API calls.
- **Lambda environment variable** (encrypted with KMS) — simpler but couples secret rotation to redeploy.

**Why SSM won:**
- Free tier (Standard parameters).
- Single static API key per source with no automatic rotation requirement.
- IAM scoping is one-line in the Lambda role.
- KMS decrypt cost is part of the SSM `GetParameter` price (free).

**Trade-offs:**
- No automatic rotation. If we ever need it, migration to Secrets Manager is one resource swap.
- Standard parameter limit of 4 KB / version — fine for an API key.

**Performance:** First `ssm.get_parameter` call adds 10–30 ms cold-start latency. Cached at module scope, so warm invocations don't re-fetch.

---

## 6. Secret value handling: `lifecycle.ignore_changes = [value]`

**Chosen:** Terraform creates the SSM parameter with placeholder `"PLACEHOLDER_SET_VIA_CLI"`, ignores future value changes; real value is set out-of-band via `aws ssm put-parameter`.

**Alternatives:**
- Pass the secret as a Terraform variable (`TF_VAR_*_api_key`) into the parameter's `value`.

**Why:**
- Keeps the plaintext API key out of Terraform state. Even with state encrypted at rest in S3, anyone with read access to state could see it.
- Lets us rotate the key with a single CLI command, no `terraform apply` needed.

**Trade-offs:**
- The first apply leaves an invalid placeholder in SSM until the manual `put-parameter` step. Easy to forget — captured in the runbook.

---

## 7. Lambda packaging: ZIP via `archive_file` + `null_resource`

**Chosen:** Local `uv pip install --target` builds deps, `archive_file` zips, Lambda references the zip directly.

**Alternatives:**
- **Container image (ECR)** — Docker build pushed to ECR; Lambda pulls the image.
- **Lambda layer** for dependencies, source as a separate ZIP.

**Why ZIP won:**
- Total package size 2 MB (just `requests` + transitive deps). No 250 MB pressure.
- Cold start with ZIP: 500 ms–1 s. Container would be 1–3 s.
- No Docker/ECR moving parts.

**Trade-offs:**
- `null_resource` runs `uv pip install` on the operator's machine; relies on Python 3.12 + uv being installed. If a future dep needs C extensions for Linux ABI, we'd need to switch to container or build inside Docker.

**Detour worth recording:**
- Initial command was `python3 -m pip install --target …` — failed because the project's `.venv` is uv-managed and pip-less, but `VIRTUAL_ENV` was set so `python3` resolved to the pip-less venv interpreter.
- Fix: `unset VIRTUAL_ENV` + `uv pip install --python python3.12 --target …`. Independent of any active venv.

---

## 8. Step Function workflow type: `STANDARD` (default)

**Chosen:** `STANDARD` workflow as the default for all pipeline orchestration.

**Alternatives:** `EXPRESS` workflows.

**Why:**
- Daily batch with auditability requirement — Standard keeps execution history for 1 year, viewable in console.
- Express is cheaper and faster for high-volume request/response traffic (≥1k/s) — irrelevant at one execution/day.
- Cost difference at our volume: pennies/month either way.

**Trade-offs:**
- Standard $25/M state transitions; Express $1/M + duration billing. At our volume both are negligible.

---

## 9. Observability: EventBridge → SNS email alerting (shared topic)

**Chosen:** Shared SNS topic `zeus-dev-alerts` in `infra/core/`. Each pipeline adds an EventBridge rule on Step Functions Execution Status Change (`FAILED`, `TIMED_OUT`, `ABORTED`) targeting the shared topic. Input transformer formats a human-readable message with execution ARN, status, and timestamps.

**Alternatives considered:**
- **Per-pipeline SNS topic** — simpler to stamp out but creates N subscriptions and N confirmation emails. Shared topic wins: one subscription, all pipelines route failures to the same inbox.
- **CloudWatch Alarms on `ExecutionsFailed` metric** — reactive but has a minimum 1-minute evaluation window and doesn't carry execution context in the notification.
- **Lambda formatter between EventBridge and SNS** — full control over subject and body, can call `describe-execution` to include failure cause inline. Rejected as over-engineering for current scale; logged as tech debt.

**Why:**
- EventBridge directly targets SNS with no added Lambda — zero new runtime surface area.
- Shared topic means new pipelines only need an EventBridge rule + target; no changes to `infra/core/` and no new subscription confirmation.
- `FAILED`/`TIMED_OUT`/`ABORTED` covers all terminal failure states for Standard workflows.

**Trade-offs:**
- `$.detail.cause` is not present in the Step Functions Execution Status Change event (only available via `describe-execution`); the alert body therefore does not include the failure reason inline. The execution ARN in the email is sufficient to retrieve it with one CLI call.
- Input transformer silently fails to deliver if a referenced JSON path is absent — learned by testing; `cause` path was removed from the transformer for this reason.

---

## 10. Snowflake integration: deferred separate scope

**Decision:** AWS-only ingestion this round. Storage integration / external stage / Snowpipe / raw table / dbt model are a separate plan once data lands in S3.

**Why:**
- Cleaner reviews — one moving target at a time.
- Snowflake side has its own cross-region IAM trust setup (different account, different region) that warrants focused design.

**When wired:**
- `snowflake_storage_integration` (account-level, shared) → `infra/core/`.
- `snowflake_stage` + `snowflake_pipe` (per-source) → each `infra/pipelines/<source>/`.

---

# Pipeline-specific decisions

## EIA daily ingestion pipeline

The first production pipeline. Pulls hourly fuel-type data from EIA Form-930 for 71 balancing authorities, daily.

### EIA-1. Orchestration: Step Functions Map (parallel fan-out per BA)

**Chosen:** Step Functions `STANDARD` workflow with a `Map` state, `MaxConcurrency=20`, one Lambda invocation per balancing authority. This is the reference pattern reused for any future fan-out batch pipeline (NOAA, EPA).

**Alternatives:**
- **Single Lambda loop** wrapped in Step Functions — sequential 71-BA loop inside one Lambda; Step Function adds only a retry envelope.
- **EventBridge → Lambda directly** — no Step Function at all.

**Why Map fan-out won:**
- 71 BAs in **9 seconds** in production (vs an estimated 5–15 minutes sequential).
- Failure isolation: one BA's API error doesn't kill the others.
- Each BA gets its own retry policy via the state machine.
- 15-minute Lambda hard timeout becomes a per-BA concern, not a per-run concern.

**Trade-offs:**
- 71 Lambda invocations vs 1 — still well inside the free tier (1M invocations/month).
- Slightly more moving pieces (extra IAM role, ASL definition).

---

### EIA-2. Lambda configuration: 512 MB / 300 s timeout / Python 3.12

**Chosen:** `memory_size = 512`, `timeout = 300`, `runtime = python3.12`.

**Why these numbers:**
- Memory 512 MB: Lambda CPU scales with memory; 512 is the sweet spot for I/O-bound HTTP work. 128 MB would throttle network throughput; 1024+ wastes money.
- Timeout 300 s: comfortable headroom — observed runtime is 1–3 s per BA. 5-min ceiling protects against API hangs.
- Python 3.12 matches the project's `.python-version`.

**Performance:**
- Cold start 500–800 ms (ZIP + 2 MB deps).
- Warm invocation: <100 ms overhead + API/S3 latency.

---

### EIA-3. Map state `MaxConcurrency`: 20

**Chosen:** 20 parallel BAs at a time.

**Alternatives:** 5, 10, 50, unbounded.

**Why 20:**
- EIA's API has rate limits (5,000/hour per key). 20 concurrent × 2 requests per BA × few seconds = well under the limit.
- AWS account default Lambda concurrency is 1,000 — 20 is 2% of that, leaves room for other workloads.
- 71 BAs / 20 ≈ 4 waves → ~9 s total. Going higher saves seconds, no real benefit.

**Trade-offs:**
- Lower would be slower; higher could trip rate limits and cause retries.

---

### EIA-4. EventBridge schedule: `cron(0 7 * * ? *)` (07:00 UTC daily)

**Chosen:** 07:00 UTC = 04:00 in `sa-east-1` (São Paulo).

**Why:**
- EIA typically publishes the previous day's data + revisions by 06:00 UTC.
- Off-peak in São Paulo — no contention with development work.
- One run per day matches the daily-batch cadence.

**Trade-offs:**
- If EIA is late publishing on a given day, we'd capture stale data and pick it up on the next run (rolling 7-day window covers this).

---

### EIA-5. Lookback strategy: rolling 7 days

**Chosen:** Each daily run pulls the last 7 days, writes to today's S3 partition. Append-only, immutable; downstream de-dup is `qualify row_number() over (partition by (period, respondent, fueltype) order by ingestion_date desc) = 1`.

**Alternatives considered:**
- **Yesterday-only lookback** — simpler, no de-dup needed downstream, but loses corrections.

**Why:**
- Catches EIA's late corrections (which "yesterday only" would silently miss).
- Bucket stays the immutable source of truth; downstream is non-destructive.

**Trade-offs:**
- Storage grows 7× faster than yesterday-only. At our volume, S3 Standard ≈ $0.014/year — free tier covers it.
- Downstream needs to know to de-dup. Owned by the dbt staging model.

---

### EIA-6. Do not coalesce API or S3 requests

**Chosen:** One Lambda per balancing authority, one EIA API call per Lambda (paginated where needed), one `s3:PutObject` per Lambda. No batching across BAs.

**Alternatives considered:**
- **Coalesce EIA API calls** — pass multiple respondents in a single `facets[respondent][]` request. 71 GETs → 1–5 GETs.
- **Coalesce S3 writes** — one combined JSON per source per day instead of 71 small files. 71 PUTs → 1 PUT.

**Why we don't coalesce:**
- Cost is already negligible: $0.0004/run on S3 PUTs, $1.50/year on Step Function transitions.
- API-side coalescing would forfeit Map's per-BA failure isolation and per-BA observability (separate log streams, separate retry counts in the Step Function console).
- S3-side coalescing would force a post-Map aggregation step (Map iterations can't write to a shared object), adding orchestration complexity.

**When to revisit:**
- A new source ships with stricter rate limits than EIA.
- Per-Lambda overhead becomes a real fraction of runtime as unit count grows past a few hundred.

---

### EIA-7. Active BA list: 71 BAs (10 permanently empty removed) + empty-rows guard

**Chosen:** Reduced the list from 81 to 71. Removed: `AEC`, `EEI`, `GLHB`, `GRIF`, `HGMA`, `NSB`, `SPA`, `WACM`, `WAUW`, `WWA`. Lambda raises `ValueError` if `fetch_ba` returns 0 rows, failing the execution and triggering the SNS alert.

**Why:**
- Confirmed via direct EIA API query (`total: "0"`) and two consecutive day's S3 partitions: these 10 BAs consistently return no data on the `electricity/rto/fuel-type-data` endpoint.
- They exist in the EIA system but do not report hourly fuel-type data via Form EIA-930.
- Keeping them generated empty 2-byte `[]` files; the empty-rows guard would now fail the pipeline daily for them.

**Trade-offs:**
- If EIA begins publishing data for one of these BAs in the future, it will go unnoticed until the list is manually updated.

---

### EIA-8. Consolidation stage: dedicated transform Lambda (post-fan-out)

**Chosen:** A separate `zeus-dev-eia-transform` Lambda, invoked once in a `Consolidate` state after the `Map` fan-out completes. It reads all raw JSON files for the day's partition, consolidates them into a single Snappy-compressed Parquet, and writes to `curated/eia/`.

**Alternatives considered:**
- **Write Parquet directly in each extract Lambda** — each BA writes its own Parquet shard. Rejected: 71 separate Parquet files per day in the curated layer, instead of one; Snowflake external stage + Snowpipe works best against a single file per partition.
- **Consolidate in Snowflake only** — skip the curated layer; Snowpipe loads raw JSON, dbt handles typing and consolidation. Rejected: a pre-typed Parquet curated layer is cheaper and faster to query from Snowflake external stage; also preserves the curated layer as a reusable artifact independent of Snowflake.
- **Post-Map consolidation inside the fan-out iterator** — not possible; each `Map` iteration is independent and cannot coordinate writes to a shared object.

**Why:**
- `Map` → `Consolidate` is a natural Step Functions pattern: wait for all parallel branches to land, then run one pass over the results.
- A single Parquet per day is the natural landing target for a Snowflake external stage + Snowpipe.
- Keeping raw and curated separate preserves the raw layer as the immutable source of truth; re-running the transform never modifies raw files.
- The transform Lambda's empty-rows guard (`ValueError` if no raw files found or all empty) means a silent failure in the fan-out is caught before the curated layer is overwritten.

**Trade-offs:**
- Two Lambda deployments per pipeline instead of one (separate source dirs, separate build artifacts, separate IAM role).
- Transform Lambda requires `s3:ListBucket` + `s3:GetObject` on `raw/eia/*` and `s3:PutObject` on `curated/eia/*`.

---

### Performance baseline (EIA, observed in production)

| Metric | Value |
|---|---|
| Step Function execution time (71 BAs) | **~9 seconds** |
| Lambda invocations per run | 71 (with `MaxConcurrency=20`) |
| Cold start estimate | 500–800 ms |
| Daily AWS cost estimate | <$0.01 (well within free tier) |
| EIA API requests per run | ~71–140 (depends on pagination) |

**Failure modes covered:**
- Per-BA Lambda retry (2 attempts) on `Lambda.ServiceException`, `Lambda.AWSLambdaException`, `Lambda.SdkClientException`, `Lambda.TooManyRequestsException`.
- API-level retry inside the Lambda on HTTP 502/503/504 with backoff (10s, 20s, 30s).
- Late-arriving EIA corrections caught by the 7-day rolling lookback.
- 0-row response → `ValueError` → SFN failure → SNS alert.
- Source-of-truth integrity preserved via append-only S3 partitions; downstream de-dup is non-destructive.
