# Architectural Decision Records

Design decisions for the Zeus data platform — an energy data ingestion and modeling system. Cross-cutting decisions (region, Terraform structure, S3 layout, secrets, packaging, observability, shared helpers) are at the top of this document. Pipeline-specific decisions are grouped per source below.

The platform runs two production ingestion pipelines — **EIA** (hourly fuel-type) and **NOAA** (daily weather summaries) — each landing data in S3 and loading it into a Snowflake landing table, plus a **digest** Lambda that emails one combined daily run-report across both. The two sources share balancing-authority codes (`ba`) so weather joins to grid data downstream.

Format per entry: the decision, alternatives considered, why the chosen option won, and known trade-offs.

---

# Cross-cutting decisions

## 1. AWS region: `sa-east-1` (São Paulo)

**Chosen:** `sa-east-1`.

**Alternatives:**
- `us-east-1` (N. Virginia) — cheapest AWS region.
- `us-west-2` (Oregon).

**Why:**
- Matches the pre-existing Terraform state backend (`zeus-analytics-tfstate` is in `sa-east-1`). Splitting infra across regions adds operational complexity.
- Local development is in Brazil; lower latency for CLI-driven tests and console use.

**Trade-offs:**
- Not the cheapest region; the cost delta is negligible at current volumes.

---

## 2. Terraform structure: decoupled multi-state roots + a shared packaging module

**Chosen:** Independent Terraform state roots. Long-lived shared infrastructure lives in `infra/core/`; the pipeline is its own root under `infra/pipelines/eia/`, instantiating the shared `infra/modules/lambda_job/` module and authoring the rest of its resources directly.

```
infra/
  core/                 # shared S3 bucket + SNS alerts topic + Snowflake warehouse/db + per-source landing stacks
    backend.tf, ..., snowflake.tf, snowflake_eia.tf, snowflake_noaa.tf
  modules/
    lambda_job/         # one Lambda + IAM role + ZIP packaging
    snowflake_landing/  # one source's Snowflake landing stack (decision 13)
  pipelines/
    eia/ noaa/ digest/  # one self-contained root each
      backend.tf, providers.tf, variables.tf, locals.tf
      remote_state.tf   # consumes infra/core outputs
      main.tf           # SSM param(s) + lambda_job + EventBridge rule + on-failure config
      outputs.tf
  build/                # gitignored Lambda zip artifacts
```

The pipeline root consumes `infra/core` outputs (`bucket_name`, `bucket_arn`, `alerts_topic_arn`) via `data "terraform_remote_state" "core"` and passes them as inputs.

**Naming convention:** `${project}-${env}-<source>-<resource>` (e.g. `zeus-dev-eia-ingest`). SSM paths: `/${project}/${env}/<source>/api_key`. Names are derived in the pipeline root from `local.prefix` + the source name.

**Alternatives considered:**
- **Single nested-module hierarchy** (`environments/dev → modules/aws → modules/aws/sources/eia`). Outputs bubble through two module layers; one bad apply could affect all shared and pipeline resources in the same plan.
- **Single TF root for everything** — collapses to one apply. Rejected: couples deploys of shared infra and pipeline resources; one bad apply could disturb the bucket, warehouse, and SNS topic.

**Why decoupled roots won:**
- **Blast radius.** A broken pipeline apply cannot touch the S3 bucket, Snowflake warehouse, or SNS topic — they live in separate state.
- **True isolation.** The pipeline is planned and applied independently of shared infra.
- **One place for cross-cutting mechanics.** Lambda packaging and IAM live in `modules/lambda_job/` and propagate on apply.

**Trade-offs:**
- `project` and `env` locals are duplicated between `infra/core/` and the pipeline root. Acceptable at this scale.

---

## 3. S3 layout: two-layer architecture, source-first prefix, multi-level Hive partitioning

**Chosen:** Single shared bucket `${prefix}-energy-data` with two data layers plus a reports prefix:
- **Raw layer** (`raw/<source>/`): one JSON file per atomic unit, immutable, append-only.
- **Curated layer** (`curated/<source>/`): one Snappy-compressed Parquet per day, produced after the fan-out completes.
- **Reports** (`reports/<source>/`): one JSON run report per run.

```
s3://zeus-dev-energy-data/raw/<source>/ingestion_year=YYYY/ingestion_month=MM/ingestion_day=DD/<unit>.json
s3://zeus-dev-energy-data/curated/<source>/ingestion_year=YYYY/ingestion_month=MM/ingestion_day=DD/<source>_grid.parquet
s3://zeus-dev-energy-data/reports/<source>/ingestion_year=YYYY/ingestion_month=MM/ingestion_day=DD/run_report.json
```

**Why:**
- **Source-first prefix** so each source has its own IAM scope and lifecycle boundary without cross-source coupling.
- **Multi-level Hive partitioning on date** keeps S3-console browsability healthy as the bucket grows. Flat date folders would become ~1,825 sibling entries per source over 5 years; multi-level keeps every depth small.
- **No `<unit>=` partition.** The unit is in the JSON payload and in the filename; a path partition would add a directory level and buy no query pruning.
- **Filename = `<unit>.json`** (raw) / `<source>_grid.parquet` (curated) keeps per-unit traceability for debugging without polluting the path.
- **Two-layer separation** keeps raw JSON as the immutable source of truth; the curated Parquet is a derived, typed artifact. Re-running the consolidation never touches raw.
- **Append-only, immutable.** Downstream de-dup happens at query time (`qualify row_number() over (partition by (period, respondent, fueltype) order by ingestion_date desc) = 1`), not at write time.

**Alternatives considered:**
- **Date-first prefix** (`raw/ingestion_date=.../<source>/...`) — better for cross-source "what did we ingest on day X" listings but worse for per-source IAM and lifecycle scoping.
- **Flat `ingestion_date=YYYY-MM-DD/`** — fewer levels but degraded console browsability over time.
- **Single raw layer only** — skip the curated Parquet. Rejected: a pre-typed Parquet is cheaper and faster to query, and the curated layer is a reusable typed artifact in its own right.

**Trade-offs:**
- Downstream must know to de-dup. That responsibility sits with whatever queries the curated/raw data.
- The curated layer is derived: a schema change does not retroactively rewrite old curated partitions.

---

## 4. Source code layout: `src/lambdas/<source>/ingest/`

**Chosen:** `src/lambdas/<source>/ingest/handler.py` + sibling modules + `requirements.txt`.

**Why:**
- `src/` is the conventional Python project root (visible to type checkers, IDE indexers, and packaging tools).
- A single `ingest/` stage holds the pipeline's source-specific code: `handler.py` (orchestration) plus `client.py` and `schema.py`. Shared, source-agnostic logic (including run reporting, `report.py`) lives in `src/shared/` (decision 11).
- Keeps Lambda source out of `extraction/` (gitignored notebooks) — a source-of-truth Lambda must be in git.

---

## 5. Secret store: SSM Parameter Store (SecureString)

**Chosen:** SSM Parameter Store, `SecureString`, AWS-managed KMS key (`alias/aws/ssm`).

**Alternatives:**
- **AWS Secrets Manager** — supports automatic rotation, $0.40/secret/month + API charges.
- **Lambda environment variable** (KMS-encrypted) — simpler but couples secret rotation to redeploy.

**Why SSM won:**
- Free tier (Standard parameters).
- A single static API key with no automatic-rotation requirement.
- IAM scoping is one line in the Lambda role.
- KMS decrypt cost is part of the SSM `GetParameter` price (free).

**Trade-offs:**
- No automatic rotation. If it's ever needed, migration to Secrets Manager is one resource swap.
- Standard parameter limit of 4 KB / version — fine for an API key.

**Performance:** First `ssm.get_parameter` call adds 10–30 ms latency. The result is cached at module scope, and the handler fetches the key once before the thread pool starts so warm invocations and concurrent workers don't re-fetch.

---

## 6. Secret value handling: `lifecycle.ignore_changes = [value]`

**Chosen:** Terraform creates the SSM parameter with placeholder `"PLACEHOLDER_SET_VIA_CLI"` and ignores future value changes; the real value is set out-of-band via `aws ssm put-parameter`.

**Alternatives:**
- Pass the secret as a Terraform variable (`TF_VAR_*_api_key`) into the parameter's `value`.

**Why:**
- Keeps the plaintext API key out of Terraform state. Even with state encrypted at rest, anyone with read access to state could otherwise see it.
- The key rotates with a single CLI command — no `terraform apply` needed.

**Trade-offs:**
- The first apply leaves an invalid placeholder in SSM until the manual `put-parameter` step. Captured in the runbook (`CLAUDE.md`).

---

## 7. Lambda packaging: ZIP via `archive_file` + `null_resource`

**Chosen:** A local `uv pip install --target` builds deps and `archive_file` zips. The zip is uploaded to S3 (`aws_s3_object` → `lambda-artifacts/<name>.zip` in the data bucket) and the Lambda references it via `s3_bucket`/`s3_key`.

**Alternatives:**
- **Container image (ECR)** — Docker build pushed to ECR; Lambda pulls the image.
- **Lambda layer** for dependencies, source as a separate ZIP.
- **Direct zip upload** (`filename` on `aws_lambda_function`) — simplest, but capped at 50 MiB zipped.

**Why ZIP-via-S3 won:**
- No Docker/ECR moving parts; the build is a local `uv` invocation.
- The zip is self-contained — no version skew between a layer and its consumers.
- The ingest package is ~49 MiB zipped (dominated by `pyarrow`, plus `snowflake-connector-python` + `cryptography`), over the 50 MiB *direct-upload* limit but well under the 250 MiB unzipped limit. S3-based deploy removes the ceiling, so a future dep bump can't silently break the deploy.

**Trade-offs:**
- `null_resource` runs `uv pip install` on the operator's machine; it relies on `uv` + Python 3.12 being on PATH. If a future dep needs a different Linux ABI, we'd switch to a container or Docker build.
- `pyarrow` + the Snowflake connector make the zip large (~49 MiB) and add ~1.2 s of cold-start init. Acceptable for a once-daily batch job.
- The artifact bucket is the shared data bucket under a `lambda-artifacts/` prefix — pragmatic at this scale; a dedicated artifacts bucket is the cleaner split if this grows.

**Detour worth recording:**
- An initial `python3 -m pip install --target …` failed because the project's `.venv` is uv-managed and pip-less, but `VIRTUAL_ENV` was set so `python3` resolved to the pip-less venv interpreter.
- Fix: `unset VIRTUAL_ENV` + `uv pip install --python python3.12 --target …`, independent of any active venv.

---

## 8. Observability: a single daily digest email + per-pipeline on-failure destinations (shared SNS topic)

**Chosen:** A shared SNS topic `zeus-dev-alerts` in `infra/core/`, fed by two paths:
1. **Combined run-report email** — each ingest Lambda only *writes* its `run_report.json` to the reports layer (succeeded/skipped, per-skip reasons, Snowflake rows loaded). A separate **digest Lambda** (`zeus-dev-reports-digest`, its own pipeline root) runs once daily after both ingest runs, reads every source's report for the day, computes each source's 30-day skip history, and publishes **one** combined email covering all sources. Adding a source = appending it to the digest's `var.sources`.
2. **Failure alert** — each ingest Lambda's asynchronous **on-failure destination** points at the topic. EventBridge invokes asynchronously with `maximum_retry_attempts = 0`, so any unhandled crash (OOM, timeout, init error) or the total-outage `ValueError` routes the failed invocation record to the topic. The digest has its own on-failure destination too.

**Alternatives considered:**
- **Per-pipeline run-report email** (the previous shape — each Lambda emailed its own summary). At one source it was fine; at N sources it's N emails/day. Replaced by the digest so the inbox gets exactly one summary regardless of source count. The per-pipeline Lambdas were slimmed accordingly (they no longer publish on success; they keep only the on-failure destination).
- **CloudWatch alarm on the Lambda `Errors` metric** — also catches every crash mode, but carries no run context and needs threshold/period tuning. The on-failure destination delivers the failed event directly with zero added configuration.
- **Per-pipeline SNS topic** — an extra subscription/confirmation per pipeline. The shared topic routes everything to one inbox.
- **Chaining the digest off the ingest Lambdas** (invoke it when the last source finishes) — couples the pipelines and needs completion signalling. A standalone scheduled digest that reads whatever reports exist is decoupled and tolerates a missing report (surfaced as "no report", since that source's own on-failure alert already fired).

**Why:**
- One email/day across all sources is the operational signal that scales — the digest reuses `src/shared/report.py` and reads the persisted `run_report.json` files, so it needs no coordination with the ingest runs.
- The on-failure destination is the per-pipeline safety net for crashes that happen *before* the report is written. It's a single `aws_lambda_function_event_invoke_config` resource per pipeline.
- The shared topic means each pipeline only needs `sns:Publish` permission (for its destination) and the digest needs `s3:GetObject`/`ListBucket` on `reports/*` — no changes to `infra/core/`.

**Trade-offs:**
- The digest fires on a schedule (08:00 UTC) rather than on completion, so it assumes both ingest runs (07:00 / 07:30 UTC) have finished — fine with the staggered crons, and a not-yet-written report just shows as "no report".
- The on-failure alert is the raw Lambda async-destination envelope (JSON), not hand-formatted. The readable summary comes from the digest; the envelope is the fallback for hard crashes.
- `maximum_retry_attempts = 0` means a transient platform error fails the run rather than auto-retrying. The daily schedule and the rolling lookback make the next run self-healing, and per-unit HTTP errors are already retried inside the Lambda.

---

## 9. Snowflake warehouse: `ZEUS_DEV_WH`

**Chosen:** A single `snowflake_warehouse` in `infra/core/` — `x-small`, auto-suspend 60 minutes, auto-resume.

**Why:**
- `x-small` is the smallest, cheapest warehouse size; auto-suspend/auto-resume means it only bills while a query runs.
- It lives in `infra/core/` because a warehouse is account-level shared infrastructure, not pipeline-specific.

**Trade-offs:**
- It backs both sources' Snowflake loads (EIA-9 + NOAA): each loader's `COPY INTO` runs on it. A single shared x-small warehouse is sufficient for these staggered daily batch loads; a per-pipeline or per-workload warehouse split would come only if loads start contending.

---

## 10. Terraform module: a single reusable `lambda_job`

**Chosen:** A shared module, `infra/modules/lambda_job/`. It packages a single Lambda: `null_resource` build (`uv pip install` + copy handler files + copy `src/shared/`) → `archive_file` → IAM role (basic execution + a caller-supplied inline policy) → `aws_lambda_function`. Source/build dirs, env vars, memory/timeout, and policy statements are inputs. The pipeline root composes everything else (SSM parameter, EventBridge rule, lambda permission, on-failure invoke config) directly. (A second shared module, `snowflake_landing`, was later added for the per-source Snowflake DDL — see decision 13.)

**Alternatives considered:**
- **A `pipeline` composition module** that wired two `lambda_job` instances, a Step Functions state machine, EventBridge, SSM, and the alert rule. This was the previous shape; it was removed because it hard-coded a two-Lambda fan-out → consolidate orchestration that the pipeline no longer uses (see EIA-1). Collapsing to one Lambda made the composition module more indirection than it removed.
- **One module per resource type** (`modules/lambda/`, `modules/eventbridge/`, …). Splits a single pipeline across multiple folders and forces the root to re-wire the pieces anyway.
- **Fully inline, no module.** Rejected: the packaging logic (build fingerprinting, archive, IAM role with inline policy) is the genuinely fiddly, reusable part and is worth keeping in one place.

**Why a single `lambda_job` module won:**
- Lambda packaging + IAM is the only logic worth abstracting; it's reused as-is for any single-Lambda need.
- A pipeline is now one Lambda plus a handful of wiring resources — small enough to read inline in the root, where the source-specific intent lives.
- Mechanics changes (runtime, build trigger, base IAM) land in one module file.

**Trade-offs:**
- The pipeline root authors its own EventBridge/SSM/alert wiring rather than getting it from a module. Across the handful of pipeline roots this is clearer, not heavier.

---

## 11. Cross-Lambda Python helpers in `src/shared/`

**Chosen:** A `src/shared/` package vendored into the Lambda's build dir at the zip root. Modules:
- `paths.py` — single source of truth for the S3 layout (`raw_key`, `raw_prefix`, `curated_prefix`, `report_key`) and its inverse `partition_date(key)`.
- `s3_io.py` — boto3 wrappers (`put_json`, `put_bytes`, `get_json`, `list_keys`, `iter_objects`).
- `ssm.py` — module-cached `get_parameter`.
- `sns.py` — `publish(topic_arn, subject, message)`.
- `snowflake_io.py` — `copy_into(...)` (key-pair connection, run one statement, return rows loaded) and `copy_statement(...)` (the one `COPY INTO … PARQUET` template builder).
- `time_window.py` — `today_utc()`, `lookback_window(today, days)` (hourly `YYYY-MM-DDTHH`, EIA), and `lookback_window_dates(today, days)` (date `YYYY-MM-DD`, NOAA).
- `report.py` — source-agnostic run-report build, per-source `format_email`, `format_digest` (combines all sources), and `skip_history`. Used by both ingest Lambdas and the digest.
- `ingest.py` — the shared daily orchestrator (`run_ingest`, `config_from_env`); each `handler.py` is a thin shim injecting the source's `fetch_fn`/`window_fn`/`schema`.

Handlers import the slices they need (e.g. `from shared import ingest, ssm, time_window`). The build step in `modules/lambda_job/` copies `src/shared/` into `${build_dir}/shared` and fingerprints `**/*.py` under both `var.src_dir` and `var.shared_dir` in `null_resource.triggers`, so any edit forces a rebuild (and a `src/shared/` change rebuilds every Lambda). `report.py` and the orchestration in `ingest.py` started as EIA-sibling code and were promoted to `src/shared/` once a second source needed them.

**Alternatives considered:**
- **Lambda Layer.** Rejected: another infra resource to manage and version-bump, for negligible size savings at this footprint.
- **Vendored copy per Lambda dir** — duplicate `paths.py` etc. into each Lambda. Defeats the point.
- **No shared library** — each handler re-implements the S3 layout and SSM caching. Re-derives the same logic in multiple places.

**Why a copied package won:**
- One edit to `src/shared/paths.py` propagates to the Lambda zip via the build fingerprint.
- The handler stays thin orchestration; the only source-specific modules are `client.py`/`schema.py`.
- No new AWS infrastructure (vs a Lambda Layer), and the zip is self-contained.

**Trade-offs:**
- The shared-side copy (`cp -r`) isn't filtered to `*.py`, so a local `__pycache__` can leak into the zip if not cleaned between builds.

---

## 12. Shared helpers are source-agnostic (raw shape owned by the caller)

**Chosen:** Everything in `src/shared/` takes the source name as a parameter and never assumes a payload shape. Raw S3 reads go through `iter_objects(bucket, prefix)`, which yields each file's parsed JSON **as-is**; the caller owns the file's shape. EIA's raw files are flat JSON arrays of rows, so the handler flattens at the call site:

```python
rows = [normalize_row(r, today)
        for obj in s3_io.iter_objects(BUCKET, prefix)
        for r in obj]
```

**Alternatives considered:**
- **A flattening reader in `src/shared/`** (`yield from json.loads(body)`). Worked for EIA but baked EIA's "raw file is a flat array of rows" assumption into shared code. A source that writes a JSON object or an envelope (`{"results": [...]}`) would break against it.
- **A `shape=` flag on the shared reader.** Rejected: pushes per-source branching into the shared layer; the call site is the natural owner of that one-liner.

**Why caller-owned shape won:**
- `src/shared/` stays a thin, assumption-free boundary: path construction, S3 IO, SSM, SNS, time math — each parameterized by `source`.
- The raw-shape decision lives next to the source-specific `schema.py`/`normalize_row`.

**Trade-offs:**
- The caller writes its own one-line flatten (or none). Negligible duplication, and it makes the per-source shape explicit rather than hidden in a shared helper.

---

## 13. Per-source Snowflake DDL as a reusable `snowflake_landing` module

**Chosen:** A second shared module, `infra/modules/snowflake_landing/`, encapsulates one source's entire Snowflake landing stack — storage integration + paired AWS IAM trust role, schema, `<SOURCE>_GRID` table, external stage over `curated/<source>/`, and the least-privilege key-pair loader user with its grants. Every name is **derived from `source_name`** (`ZEUS_DEV_<SOURCE>_S3_INT`, `<SOURCE>_GRID`, `<SOURCE>_STAGE`, `ZEUS_DEV_<SOURCE>_LOADER`, …); the table columns are a `list(object({name,type}))` input. `infra/core` calls it once per source (`module "eia_landing"`, `module "noaa_landing"`); the shared `ZEUS_DEV` database lives in `snowflake.tf`, passed in as a name.

**Alternatives considered:**
- **Copy `snowflake_eia.tf` → `snowflake_noaa.tf`** (duplicate ~200 lines of inline DDL per source). Simple and zero churn, but every fix has to be applied in N places and the regularity (integration + IAM + schema + table + stage + loader + grants) is exactly what a module captures.
- **Leave EIA inline, only modularize NOAA.** Asymmetric — the two sources would drift, and the module wouldn't be exercised by the established pipeline.

**Why a shared module won:**
- The Snowflake stack is highly regular across sources; only names + the column list vary. One module call per source is the DRY-est expression and keeps the two stacks structurally identical.
- New sources are a single module block, not a copy-paste of the DDL.

**Trade-offs / the migration:**
- Moving EIA's already-live resources into the module changed their state addresses, so it required a one-time `terraform state mv` per resource (integration, IAM role + policy, schema, table, stage, loader role + user, `grant_account_role`, and the 5 privilege grants) — **not** a destroy/recreate. The module reproduces every name and comment byte-for-byte so the post-move `terraform plan` is "No changes" (the gate before applying). One benign exception: the EIA stage's `file_format` shows a perpetual in-place diff from the snowflake provider normalizing the string (`NULL_IF = []`, lower-case `true`) — pre-existing, harmless, identical to the old inline config.
- The module uses two providers (aws + snowflake); it declares `required_providers` and inherits the default provider configs from `infra/core`.

---

# Pipeline-specific decisions

## EIA pipeline

Pulls hourly fuel-type data from EIA Form-930 for 71 balancing authorities, daily.

### EIA-1. Orchestration: a single Lambda with an in-process thread fan-out

**Chosen:** One Lambda, `zeus-dev-eia-ingest`, invoked directly by EventBridge. It fans out the per-BA fetch across a `ThreadPoolExecutor` (`MAX_WORKERS` env var, default 20), writes one raw JSON file per BA, then — in the same invocation — reads back the day's raw partition and consolidates it into a single curated Parquet and writes a run report. It does **not** email — the daily digest Lambda (decision 8) sends the combined summary.

**Alternatives considered:**
- **Step Functions `Map` fan-out + a separate consolidation Lambda.** This was the previous architecture: a `STANDARD` workflow with a `Map` state (one extract Lambda per BA) feeding a `Consolidate` task. Replaced because the orchestration outweighed the workload — the full run takes ~12.5 s, comfortably inside a single Lambda's limits, and a state machine plus a second Lambda plus their IAM roles and the failure-alert rule were more moving parts than the job warranted.
- **A sequential `for` loop over BAs.** Simplest, but ~71 paginated HTTP fetches in series would stretch the run substantially. The thread pool keeps it parallel without an orchestrator.

**Why a single threaded Lambda won:**
- 71 BAs complete in ~12.5 s — far inside the 300 s timeout, even with the consolidation pass.
- `ThreadPoolExecutor` gives the parallelism the `Map` state used to provide (the work is I/O-bound HTTP, which threads handle well).
- Per-BA failure is handled in-process: each worker catches its own errors and returns a `skipped` result; the run continues. No orchestrator needed for fault isolation.
- Far less infrastructure: no state machine, no second Lambda, no extra IAM role, no execution-status alert rule.

**Trade-offs:**
- No Step Functions execution graph in the console. Per-run visibility is the daily digest email plus CloudWatch logs/metrics; per-BA forensics means reading logs rather than clicking a branch.
- Retry is whole-run, not per-BA. At ~12.5 s a full re-run is cheap, and per-BA HTTP errors are already retried inside the client.

---

### EIA-2. Lambda configuration: 1024 MB / 300 s timeout / Python 3.12

**Chosen:** `memory_size = 1024`, `timeout = 300`, `runtime = python3.12`.

**Why these numbers:**
- Memory 1024 MB: the single Lambda holds up to `MAX_WORKERS` BAs' row-sets in flight during the fan-out *and* builds the full-day pyarrow table during consolidation. Observed peak is ~247 MB, so 1024 MB leaves comfortable headroom; Lambda CPU also scales with memory, which helps the pyarrow write.
- Timeout 300 s: observed full run is ~12.5 s. The 5-minute ceiling is headroom against a slow EIA API.
- Python 3.12 matches the project's `.python-version`.

**Performance:**
- Cold-start init ~1.2 s (ZIP + `pyarrow`).
- Full 71-BA run ~12.5 s, peak memory ~247 MB.

---

### EIA-3. Fan-out concurrency: `MAX_WORKERS = 20`

**Chosen:** 20 worker threads fetching BAs in parallel.

**Alternatives:** 5, 10, 50, unbounded.

**Why 20:**
- EIA's API has rate limits. 20 concurrent × a couple of paginated requests per BA stays well under them.
- 71 BAs across 20 workers is roughly four waves — fast enough that going higher saves little.

**Trade-offs:**
- Lower would be slower; higher risks tripping rate limits and triggering the client's backoff retries.

---

### EIA-4. EventBridge schedule: `cron(0 7 * * ? *)` (07:00 UTC daily)

**Chosen:** 07:00 UTC = 04:00 in `sa-east-1` (São Paulo).

**Why:**
- EIA typically publishes the previous day's data + revisions by 06:00 UTC.
- Off-peak in São Paulo — no contention with development work.
- One run per day matches the daily-batch cadence.

**Trade-offs:**
- If EIA is late publishing on a given day, the run captures stale data and picks up the revision on the next run (the rolling 7-day window covers this).

---

### EIA-5. Lookback strategy: rolling 7 days

**Chosen:** Each daily run pulls the last 7 days and writes to today's S3 partition. Append-only, immutable; downstream de-dup is `qualify row_number() over (partition by (period, respondent, fueltype) order by ingestion_date desc) = 1`.

**Alternatives considered:**
- **Yesterday-only lookback** — simpler, no de-dup needed downstream, but loses EIA's late corrections.

**Why:**
- Catches EIA's late corrections that "yesterday only" would silently miss.
- The bucket stays the immutable source of truth; downstream consumption is non-destructive.

**Trade-offs:**
- Storage grows faster than yesterday-only. At this volume the cost is within the free tier.
- Downstream must de-dup.

---

### EIA-6. Do not coalesce API or S3 requests

**Chosen:** One EIA API call per balancing authority (paginated where needed) and one `s3:PutObject` per BA. No batching across BAs.

**Alternatives considered:**
- **Coalesce EIA API calls** — pass multiple respondents in one `facets[respondent][]` request.
- **Coalesce S3 writes** — one combined JSON per day instead of 71 small files.

**Why we don't coalesce:**
- Cost is already negligible (a handful of cents/month for S3 PUTs at this volume).
- One file per BA preserves per-BA traceability: a missing or stale raw file pinpoints exactly which BA had a problem, and the per-BA skip semantics depend on each fetch being independent.
- A combined-write approach would couple all BAs into one object, so a single bad BA could corrupt or block the whole file.

---

### EIA-7. Active BA list: 71 BAs (10 permanently empty removed) + per-BA skip handling

**Chosen:** The list was reduced from 81 to 71. Removed: `AEC`, `EEI`, `GLHB`, `GRIF`, `HGMA`, `NSB`, `SPA`, `WACM`, `WAUW`, `WWA`. A BA that returns 0 rows (or whose fetch errors) is recorded as `skipped` and the run continues; only a total outage fails the run.

**Why:**
- Confirmed via direct EIA API query (`total: "0"`) across consecutive days: these 10 BAs consistently return no data on the `electricity/rto/fuel-type-data` endpoint. They exist in the EIA system but don't report hourly fuel-type data via Form EIA-930.
- Keeping them generated empty `[]` files and a daily skip entry for no signal.
- Per-BA skip (rather than hard-fail) keeps one flaky or empty BA from sinking the entire daily run; the skip is recorded in the run report and surfaced in the daily digest email, including a 30-day skip-frequency history.

**Trade-offs:**
- If EIA begins publishing data for one of the removed BAs, it goes unnoticed until the list is manually updated.
- A BA that silently starts returning empty shows up as a skip in the email rather than a hard failure — visible, but not a page.

---

### EIA-8. Consolidation: in-process, after the fan-out

**Chosen:** After the thread fan-out finishes, the same Lambda lists the day's `raw/eia/...` partition, reads every file, normalizes each row (`schema.normalize_row`), and writes one Snappy-compressed Parquet to `curated/eia/...`. It then writes the run report, and finally raises `ValueError` if zero rows were consolidated.

**Alternatives considered:**
- **A separate consolidation Lambda** (the previous `transform` worker invoked by a Step Functions `Consolidate` task). Removed with the orchestrator — see EIA-1.
- **Write Parquet directly in each per-BA fetch** — produces 71 Parquet shards/day instead of one. A single file per partition is the cleaner curated target.
- **Skip the curated layer; consolidate at query time** — rejected: a pre-typed single Parquet is cheaper and faster to query and keeps the curated artifact independent of any query engine.

**Why:**
- Reading the raw partition back (rather than consolidating in-memory from the fetch results) means the consolidation reflects exactly what landed in S3 — partial fan-outs consolidate cleanly.
- Keeping raw and curated separate preserves raw as the immutable source of truth; re-running never modifies raw files.
- The total-outage guard (`ValueError` when zero rows consolidated) turns a silent empty day into a hard failure that reaches the on-failure destination.

**Trade-offs:**
- Consolidation reads the whole day's prefix, so a same-day re-run includes any earlier writes for that day and overwrites the curated Parquet. This is idempotent by key and matches the append-only/overwrite-by-key contract.
- The Lambda needs `s3:ListBucket` + `s3:GetObject` on `raw/eia/*` and `reports/eia/*`, and `s3:PutObject` on `curated/eia/*` and `reports/eia/*`.

---

### EIA-9. Snowflake load: `COPY INTO` a landing table from an external stage

**Chosen:** After writing the day's curated Parquet, the same Lambda runs one `COPY INTO ZEUS_DEV.EIA.EIA_GRID` from an external stage (`EIA_STAGE`) over `curated/eia/`. Snowflake reads the Parquet from S3 itself via a storage integration (`ZEUS_DEV_EIA_S3_INT` + a paired AWS IAM role); data never streams through the Lambda. The Lambda authenticates as a least-privilege key-pair service user (`ZEUS_DEV_EIA_LOADER`, USAGE + INSERT only). `EIA_GRID` is an append-only landing table: duplicates from the 7-day lookback overlap (EIA-5) are deduped downstream in dbt on `(period, respondent, fueltype)` keeping the latest `ingestion_date`, mirroring the S3 raw-layer contract (decision 3). All Snowflake DDL lives in `infra/core/` alongside the warehouse.

**Alternatives considered:**
- **Snowpipe auto-ingest** (S3 event → SQS → Snowpipe). More decoupled, but needs an SQS queue, the pipe, and S3 notification wiring for the same outcome; the Lambda already knows when the Parquet is ready.
- **Stream rows through the Lambda** (`INSERT` via the connector). Pushes the whole day's data through the function's memory; `COPY`-from-stage lets Snowflake read S3 directly.
- **Password auth for the loader.** Rejected: a password would land in tfstate or need an out-of-band `ALTER USER`. Key-pair keeps only the public key in Terraform; the private key lives in SSM (`/zeus/dev/snowflake/eia_loader_private_key`), set out-of-band — the same contract as the API key (decisions 5–6).
- **Write-time dedup (`MERGE`).** Rejected: it discards the revision history the overlapping lookback is designed to capture, and is more complex than an append + downstream `qualify`.

**Why:**
- Shortest path that lands rows in the same invocation, with no new AWS runtime infrastructure — just the storage integration, an IAM role, and an SSM secret.
- `FILE_FORMAT = (TYPE = PARQUET USE_LOGICAL_TYPE = TRUE)` is **required**: without it Snowflake reads the Parquet `period` as a raw INT64 and the timestamp loads as "Invalid date".
- Snowflake's per-file load metadata makes a same-day re-run idempotent (the already-loaded Parquet is skipped); cross-day overlap is the intended duplication, resolved downstream.
- A load failure is recorded in the run report, then re-raised so the async on-failure destination alerts (decision 8) — the curated Parquet stays safe in S3 and the load is re-runnable.

**Trade-offs:**
- The append-only landing table carries ~7× row duplication from the lookback overlap; the deduped view is dbt's job (not yet built).
- The storage integration and service user require `ACCOUNTADMIN` to create, so `infra/core/`'s Snowflake provider runs as `ACCOUNTADMIN`.
- `snowflake-connector-python` declares loose `pyOpenSSL`/`cryptography` bounds that resolve to an import-incompatible pair; both are pinned (`cryptography==43.0.3`, `pyOpenSSL==24.2.1`). Adding the connector pushed the zip to ~49 MiB, motivating the S3-based Lambda deploy (decision 7).
- History is loaded once by a whole-stage backfill (`backfill/eia/snowflake_load.py`) that reuses the same `COPY` helper; it must not be re-run (load metadata expires after 64 days, which would re-load old files as duplicates).

---

### Performance baseline (EIA, observed in production)

| Metric | Value |
|---|---|
| Full run (71 BAs) | **~12.5 seconds** |
| Lambda invocations per run | 1 (internal fan-out across 20 threads) |
| Peak memory used | ~247 MB (of 1024 MB) |
| Cold-start init | ~1.2 s |
| Daily AWS cost estimate | <$0.01 (well within free tier) |
| EIA API requests per run | ~71–140 (depends on pagination) |

**Failure modes covered:**
- Per-BA error or 0-row response → recorded as `skipped`, run continues.
- API-level retry inside the client on HTTP 502/503/504 with backoff (10 s, 20 s, 30 s).
- Late-arriving EIA corrections caught by the 7-day rolling lookback.
- Total outage (0 rows consolidated) → `ValueError` → failed async invocation → on-failure destination → SNS alert.
- `run_report.json` written on every completed run; the daily digest (decision 8) turns it into a combined email.
- Source-of-truth integrity preserved via append-only S3 partitions; downstream de-dup is non-destructive.

---

## NOAA pipeline

Pulls daily weather summaries from NOAA NCEI (`access/services/data/v1`, `daily-summaries`) for weather stations grouped under 14 balancing authorities (`CISO, PJM, ERCO, MISO, ISNE, NYIS, SWPP, TVA, SOCO, DUK, FPL, BPAT, PSCO, SRP`; 3–10 stations each), daily. Mirrors the EIA pipeline's shape; only the genuinely source-specific decisions are recorded here.

### NOAA-1. Reuse the EIA pipeline shape; no API key

**Chosen:** Same single-Lambda, EventBridge-direct, fan-out → consolidate → `COPY INTO` → write-report orchestration as EIA (EIA-1), via the shared `src/shared/ingest.py` orchestrator. The NCEI `data/v1` endpoint needs **no token**, so NOAA drops the api-key SSM parameter and the `ssm.get_parameter(API_KEY)` fetch entirely — only the Snowflake loader key remains.

**Why:** the EIA pipeline was already factored so that only `client.py` + `schema.py` are source-specific; both handlers are now ~12-line shims over `ingest.run_ingest`, injecting the source's `fetch_fn` (NOAA's needs no api key) and `window_fn` (daily vs hourly). No API key means one fewer secret and IAM statement.

### NOAA-2. Fan-out unit = BA; all of a BA's stations in one batched NCEI request

**Chosen:** The fan-out unit is the **BA** (one raw `<ba>.json` per BA, mirroring EIA), and the BA→station map (3–10 stations each) lives in `client.py`'s `STATIONS`. The client fetches all of a BA's stations in **one batched request** (comma-separated `stations=`), tagging every row with its `ba`.

**Alternatives considered:**
- **One request per station** (as the exploratory notebook did). Works, but multiplies request count (~7× here) for data-identical results; NCEI can also 429/503 under high concurrency.
- **Unit = station** (one raw file per station). Breaks the EIA `{"units":[...]}` event contract and the per-unit fault-tolerance granularity, and makes the `ba` join key implicit.

**Why:** batched-per-BA is data-identical to per-station (NCEI returns the same rows), keeps the raw layout and event contract identical to EIA, and minimizes request volume against a fragile API.

**Trade-offs:** a single failing station fails its whole BA (coarse, but logged and skip-reported); the `ba` tagging is added client-side, not from the API.

### NOAA-3. Wide schema, 13 datatypes, grain `(ba, station, date)`

**Chosen:** One row per `(ba, station, date)`, **wide** — one column per datatype: `TMAX, TMIN, TAVG, PRCP, SNOW, SNWD, AWND, WSF2, WSF5, WDF2, RHAV, ASLP, ADPT` (metric units) plus `ingestion_date`. Absent datatypes land null. Loaded into `ZEUS_DEV.NOAA.NOAA_GRID`.

**Alternatives considered:**
- **The notebook's 8 datatypes** (`TMAX,TMIN,PRCP,AWND,RHAV,ASLP,ADPT,WSF5`). The chosen set adds `TAVG` (demand/degree-days), `SNOW`/`SNWD` (winter demand), and `WSF2`/`WDF2` (wind gusts/direction) for the weather↔energy use case.
- **Long format** (one row per station/date/datatype). Mirrors EIA's tall shape but multiplies row count; wide is the natural shape for daily station weather and joins cleanly to grid data on `ba`.

**Why:** wide is sparse-tolerant (Parquet/Snowflake store nulls cheaply) and matches how the data is consumed (weather features per station-day). `USE_LOGICAL_TYPE = TRUE` is required for the `date` column to load as a real DATE (raw INT32 otherwise).

### NOAA-4. Rolling 7-day lookback + `ingestion_date` (same contract as EIA-5)

**Chosen:** Each run pulls the last 7 days (`lookback_window_dates`) into today's partition; append-only, deduped downstream on `(date, station)` keeping the latest `ingestion_date`.

**Why:** NCEI daily-summaries are **provisional and lag** — recent days arrive late (the smoke test's 7-day window only had data through 3 days prior) and get QC-revised. The overlap catches both, exactly as EIA's lookback catches EIA's corrections. Schedule is `cron(30 7 * * ? *)`, staggered 30 min after EIA so the two daily runs don't overlap.

### NOAA-5. Historical backfill: two-phase, day-partitioned; force IPv4 from local dev

**Chosen:** `backfill/noaa/` mirrors `backfill/eia/`: `run.py extract` fetches each BA's whole date range in one batched request and writes one raw JSON per `(BA, observation-day)` (partition **backdated** to the observation date); `run.py transform` writes one curated Parquet per observation-day; `snowflake_load.py` whole-stage `COPY`s. Both phases skip already-written keys for idempotent resume, or take `--overwrite` to re-write them (see NOAA-6).

**Why / the lesson:**
- This produces the same per-observation-day S3 layout as the daily pipeline (and as EIA's backfill), not one blob in the run-date partition — so the curated layer is uniform across backfill and daily runs.
- **Force IPv4 for NCEI from local dev.** `ncei.noaa.gov` is dual-stack (publishes both A and AAAA records), but the dev machine has a broken IPv6 route; Python's `urllib3` prefers IPv6 and stalls **~10 min/request** on the dead route, while `curl` dodges it via Happy Eyeballs and the Lambda's AWS network is unaffected. `run.py` sets `urllib3_cn.allowed_gai_family = lambda: socket.AF_INET`, cutting a request from ~10 min to **~2 s (~300×)**. This was previously misdiagnosed as NCEI peak-hour throttling and "run off-peak" — it was the IPv6 stall the whole time (a payload-independent ~520 s "latency" is the tell: it's connect time, not transfer). NCEI can still 429/503 under heavy concurrency, which the `fetch.py` retry wrapper backs off on.

**Trade-offs:** the backfill is the heaviest S3 user (~6,000 curated days × 14 BAs ≈ ~84k raw `(BA,day)` files for 2010→now) — by design, matching EIA's per-day layout. S3 request cost is still well under $1.

### NOAA-6. Widening BA coverage (4 → 14) and re-backfilling with `--overwrite`

**Chosen:** Expanded `STATIONS` and `locals.tf` from the original 4 BAs to 14 (added `ISNE, NYIS, SWPP, TVA, SOCO, DUK, FPL, BPAT, PSCO, SRP` — large, geographically distinct EIA BAs), so the weather↔grid join on `ba` covers more of the grid. To add their history to an already-backfilled source, both backfill phases gained an `--overwrite` flag: extract re-fetches every BA, and transform **rebuilds every daily curated Parquet** — because the curated grain bundles all BAs into one file per day, a new BA can't be added without rewriting that day's file.

**Alternatives considered:**
- **Clean delta load** — write new-BA-only curated files under a distinct filename and `COPY` just those, touching no existing data and creating no duplicates. Correct and zero-duplication, but more one-off code.
- **Defer history** — let the daily Lambda collect the new BAs forward-only. Rejected: loses the 2010→now history NCEI has and the weather join needs.

**Why overwrite won:** simplest to run with the existing tooling; the landing table is append-only and deduped downstream on `(date, station)`, so re-loading the original 4 BAs as duplicates is tolerated and cleaned in the (future) dbt layer.

**Trade-offs:** the whole-stage re-`COPY` reloads every curated file (new etags), duplicating the original 4 BAs' full history in `NOAA_GRID` until the downstream dedup runs — a deliberate, one-time exception to the backfill's usual "run once" rule.
