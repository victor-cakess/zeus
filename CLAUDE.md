# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python 3.12+ project managed with `uv`. The repo is a data platform for energy data ingestion and modeling. One Step Functions state machine (`zeus-dev-daily-pipeline`) runs the whole daily flow: two ingestion pipelines in parallel land data in S3 and load it into Snowflake landing tables — **EIA** (hourly fuel-type → `ZEUS_DEV.EIA.EIA_GRID`) and **NOAA** (daily weather summaries → `ZEUS_DEV.NOAA.NOAA_GRID`) — then a container-image Lambda (`zeus-dev-dbt-run`) runs `dbt build` (models + tests) over `transform/`, then `zeus-dev-reports-digest` emails one combined run-report covering both sources plus the dbt run. The digest step **always runs**, even when an upstream step failed. The two sources are deliberately keyed on the same balancing-authority codes (`ba`) so weather joins to grid data downstream.

## Repository layout

```
.github/workflows/ci.yml          # PR/push offline gates: gitleaks + dbt parse + terraform fmt/validate (see Working norms → CI)
infra/
  core/                           # Terraform root: shared S3 bucket + SNS alerts topic + Snowflake warehouse/db + per-source Snowflake DDL
    backend.tf, providers.tf, variables.tf, locals.tf, main.tf, sns.tf, outputs.tf
    snowflake.tf                  # shared ZEUS_DEV database
    snowflake_eia.tf              # module "eia_landing"  — EIA storage integration + ZEUS_DEV.EIA DDL + loader
    snowflake_noaa.tf             # module "noaa_landing" — NOAA storage integration + ZEUS_DEV.NOAA DDL + loader
  modules/
    lambda_job/                   # one Lambda + IAM role + ZIP-via-S3 packaging
    snowflake_landing/            # one source's Snowflake landing stack (integration + IAM trust + schema/table/stage + key-pair loader); names derived from source_name
  pipelines/
    eia/                          # EIA pipeline root: SSM (api key + snowflake key) + lambda_job; exports function_arn + balancing_authorities
    noaa/                         # NOAA pipeline root: SSM (snowflake key only — NCEI needs no api key) + lambda_job; same outputs
    digest/                       # digest pipeline root: lambda_job (reads all sources' run reports + the dbt report, emails one summary)
    dbt/                          # dbt pipeline root: ECR repo + docker build/push + container-image Lambda + transformer-key SSM
    orchestration/                # daily state machine root: SFN + EventBridge cron + IAM; consumes the other roots' outputs (apply LAST)
      backend.tf, providers.tf, variables.tf, remote_state.tf, locals.tf, main.tf, outputs.tf   # (same file set in each pipeline root)
  build/                          # gitignored — Lambda zip artifacts (one dir + zip per Lambda)
src/
  shared/                         # importable by every Lambda; copied into each build at zip root
    paths.py                      # raw_key / raw_prefix / curated_prefix / report_key — single S3-layout owner
    s3_io.py                      # put_json / put_bytes / get_json / list_keys / iter_objects
    ssm.py                        # cached get_parameter
    sns.py                        # publish(topic_arn, subject, message)
    snowflake_io.py               # copy_into(...) — key-pair connection, run a statement, return rows loaded
    time_window.py                # today_utc / lookback_window (hourly) / lookback_window_dates (daily)
    report.py                     # source-agnostic run-report + dbt-report build + per-source email + format_digest + skip history
  lambdas/
    eia/ingest/                   # EIA Lambda: handler.py (orchestration) + client.py (paginated HTTP) + schema.py (dash→underscore) + requirements.txt
    noaa/ingest/                  # NOAA Lambda: handler.py + client.py (NCEI daily-summaries, batched stations, no token) + schema.py (wide, 13 datatypes) + requirements.txt
    digest/                       # digest Lambda: handler.py reads each source's run_report.json + the dbt report, sends one combined email (requirements.txt: boto3 only)
    dbt/                          # dbt runner Lambda (container image): Dockerfile (bakes transform/ + dbt deps) + handler.py (thin: key → patches → dbt build → report) + lambda_mp_patch.py (/dev/shm patches) + requirements.txt
transform/                        # dbt project (staging + intermediate models + tests; profiles.yml env-var driven, key-pair auth) — COPYed into the dbt image at build
  DECISIONS.md                    # modeling/business-rule decision records (M-N entries; infra ADRs stay in README.md)
extraction/                       # gitignored, exploratory notebooks
backfill/                         # one-off historical backfill scripts (reuse src/ via _bootstrap.py); see backfill/README.md
  eia/                            # fetch→raw→curated→COPY: run.py (extract/transform) + fetch.py + extract.py + transform.py + snowflake_load.py + units.py (BA list) + _bootstrap.py + logconf.py
  noaa/                           # same two-phase pattern (run.py/fetch/extract/transform/snowflake_load + _bootstrap + logconf; stations from src client, no units.py); day-partitioned, backdated, idempotent resume
README.md                         # project overview + architectural decisions
```

## Pipelines currently live

### Daily orchestration (production)

- **One state machine runs everything:** `zeus-dev-daily-pipeline` (root `infra/pipelines/orchestration/`). EventBridge rule `zeus-dev-daily-pipeline` fires `cron(0 7 * * ? *)` (07:00 UTC = 04:00 sa-east-1) → `states:StartExecution` with input `{}` (the BA payloads are baked into the definition).
- **Shape:** `Ingest` (Parallel: EIA + NOAA branches) → `Dbt` → `Digest` → `CheckFailures` (Choice) → `Success` / `NotifyFailure` → `Fail`. Each ingest branch **catches its own failure** and normalizes to `{source, failed}` Pass states, so the Parallel always completes; the Dbt step's Catch routes straight to the digest — **the digest always runs**. `CheckFailures` inspects `$.ingest[i].failed` / `$.dbtError` / `$.digestError`; on any failure it publishes the full execution state to `zeus-dev-alerts` and marks the execution **Failed** (red in the console / CloudWatch metrics).
- **Retries:** each `lambda:invoke` retries only the AWS-transient set (`Lambda.ServiceException`, `TooManyRequests`, `SdkClient`, `AWSLambda`; 2 attempts, backoff 2.0). Function errors (crashes, total outage) go straight to the branch Catch — retrying a code bug just doubles the run.
- **Visibility:** CloudWatch logging `level = ALL` + execution data (log group `/aws/vendedlogs/states/zeus-dev-daily-pipeline`), X-Ray tracing enabled.
- **Single source of truth:** one `ingest_sources` local (fed by the eia/noaa roots' outputs via `terraform_remote_state`) derives the ingest Branches, the `CheckFailures` `$.ingest[i]` rules, and the SFN role's invoke list — adding a source is one entry + its remote-state block, and branch order can't diverge from the failure checks. The dbt/digest ARNs come from their roots' outputs the same way; the orchestration root duplicates nothing and must be **applied last**.
- **Deliberately NOT a per-BA fan-out:** the 71-way fan-out stays inside the ingest Lambda's thread pool (worst observed daily run 59 s vs the 300 s timeout); SFN orchestrates at the source level only, and per-BA detail lives in the run reports + digest.
- **Observed:** full execution (parallel ingest → dbt → digest) ~30 s end-to-end.

### EIA daily ingestion (production)

- **Trigger:** the daily state machine invokes the Lambda **synchronously** (`lambda:invoke`) with `{"units": [...]}` — the full BA list exported by `infra/pipelines/eia/` (`balancing_authorities` output) and baked into the state machine definition.
- **Worker:** a single Lambda `zeus-dev-eia-ingest` (Python 3.12, 1024 MB, 300 s timeout, deployed via S3). One invocation does the whole run:
  1. Fans out the per-BA fetch across a `ThreadPoolExecutor` (`MAX_WORKERS` env var, default 20), writing one raw JSON file per BA.
  2. Reads back the day's raw partition and consolidates every row into one Snappy-compressed Parquet in the curated layer.
  3. Runs `COPY INTO ZEUS_DEV.EIA.EIA_GRID` from the external stage to load that day's Parquet into Snowflake (Snowflake reads S3 directly via the storage integration).
  4. Writes a run report (`run_report.json`) to the reports layer. It does **not** email — the daily digest Lambda emails the combined summary (see below).
- **Active units:** 71 balancing authorities in `infra/pipelines/eia/locals.tf`.
- **Secrets:** EIA API key in SSM `/zeus/dev/eia/api_key`; Snowflake loader private key in SSM `/zeus/dev/snowflake/eia_loader_private_key` (both `SecureString`). Lambda role has scoped `ssm:GetParameter` + `kms:Decrypt` on both. Each is fetched per run before it's needed.
- **Raw output:** `s3://zeus-dev-energy-data/raw/eia/ingestion_year=YYYY/ingestion_month=MM/ingestion_day=DD/<unit>.json`. Append-only, immutable; rolling 7-day lookback per run. Overlapping windows are intentional and de-duplicated downstream at query time on `(period, respondent, fueltype)`.
- **Curated output:** `s3://zeus-dev-energy-data/curated/eia/ingestion_year=YYYY/ingestion_month=MM/ingestion_day=DD/eia_grid.parquet`. Single Snappy-compressed Parquet per day, produced by the ingest Lambda from that day's raw files.
- **Reports output:** `s3://zeus-dev-energy-data/reports/eia/.../run_report.json` — one per run, listing succeeded and skipped units (with reasons).
- **Per-BA fault tolerance:** a BA that errors or returns 0 rows is recorded as `skipped` and the run continues, consolidating whatever landed. Only a **total outage** (0 rows consolidated) raises `ValueError` and fails the invocation.
- **Alerting:** failure routing lives in the state machine. Any unhandled crash (OOM, timeout, init error) or the total-outage `ValueError` is caught by the EIA branch's Catch; the digest still runs (EIA shows as "no report"), then `CheckFailures` publishes to `zeus-dev-alerts` and fails the execution. (Success-path summaries come from the digest, not per-pipeline.)
- **Observed performance:** production daily runs 18–59 s, avg ~40 s (cold start + EIA API latency variance; CloudWatch `Duration`, June 2026 — a warm smoke-test run is ~12.5 s). Peak memory ~247 MB.

### NOAA daily ingestion (production)

- **Trigger:** the daily state machine invokes `zeus-dev-noaa-ingest` synchronously, **in parallel with EIA**, with `{"units": [...]}` — the 14-BA list (`CISO, PJM, ERCO, MISO, ISNE, NYIS, SWPP, TVA, SOCO, DUK, FPL, BPAT, PSCO, SRP`) exported by `infra/pipelines/noaa/`. Nothing the two pipelines touch contends (different APIs, S3 prefixes, Snowflake tables/users).
- **Worker:** single Lambda (Python 3.12, 1024 MB, 300 s, deployed via S3). Same orchestration shape as EIA: fan out per-BA fetch → write one raw JSON per BA → consolidate the day's partition to one curated Parquet → `COPY INTO ZEUS_DEV.NOAA.NOAA_GRID` → write `run_report.json`. No API key (NCEI `daily-summaries` needs none). `lookback_window_dates` gives a rolling 7-day date window (NOAA data lags a few days, so the lookback catches late/QC-revised days).
- **Fan-out unit = BA.** Each of the 14 BAs maps to 3–10 weather stations (the `STATIONS` map in `src/lambdas/noaa/ingest/client.py`); the client fetches all of a BA's stations in **one batched NCEI request** (comma-separated `stations=`) and tags every row with its `ba`. Raw layout is one `<ba>.json` per BA, mirroring EIA.
- **Grain / schema:** one row per `(ba, station, date)`, **wide** — 13 datatypes (`TMAX, TMIN, TAVG, PRCP, SNOW, SNWD, AWND, WSF2, WSF5, WDF2, RHAV, ASLP, ADPT`), metric units, plus `ingestion_date`. Absent datatypes land null.
- **Secrets:** Snowflake loader key only, SSM `/zeus/dev/snowflake/noaa_loader_private_key`. No api-key parameter.
- **Outputs:** `raw/noaa/.../<ba>.json`, `curated/noaa/.../noaa_grid.parquet`, `reports/noaa/.../run_report.json` — same partition scheme as EIA.
- **Observed performance:** production daily runs 5–50 s, avg ~13.5 s (CloudWatch `Duration`, June 2026).

### dbt build (production)

- **Trigger:** the `Dbt` state, between the ingest Parallel and the digest. Invoked synchronously with `{}`.
- **Worker:** container-image Lambda `zeus-dev-dbt-run` (root `infra/pipelines/dbt/`; image in ECR repo `zeus-dev-dbt-run`, tag = content hash over Dockerfile/runner `*.py`/`transform/`/`src/shared/`; 2048 MB, 300 s). The image bakes `transform/` + `dbt deps` at build time; the handler runs `dbt build` (models + tests) via `dbtRunner`, authenticating as `ZEUS_DEV_TRANSFORMER` (key-pair; private key SSM `/zeus/dev/snowflake/transformer_private_key`, fetched to `/tmp` per run).
- **Report-before-raise:** writes `reports/dbt/.../run_report.json` (`models_built`, `tests_passed`/`tests_failed`, failed-test names) **before** raising on failure — so the digest email always carries the dbt detail. A `dbt build` failure (including failing tests) fails the step; the Catch routes to the digest, then `CheckFailures` alerts + fails the execution.
- **Lambda runtime gotchas (isolated in `src/lambdas/dbt/lambda_mp_patch.py`, rationale in its docstring; the handler calls `apply()`):** Lambda has **no `/dev/shm`**, so multiprocessing SemLocks raise `FileNotFoundError` — `apply()` swaps dbt's mp context for `multiprocessing.dummy` **before** the `dbt.cli` import (Manifest binds the lock factory at class-definition time) and replaces ThreadPool's SemLock-backed change notifier with an `os.pipe()` shim. The image is read-only → `HOME` and dbt's target/log paths are redirected to `/tmp`.
- **Observed performance:** ~23 s per run (4 models, 14 tests), image cold-start init ~3.8 s, peak memory ~273 MB (of 2048).

### Daily digest (production)

- **Trigger:** the final state-machine step — runs **always**, even when an ingest or dbt step failed (their Catches route to it). Invoked synchronously with `{}` (`zeus-dev-reports-digest`, 256 MB, 60 s).
- **What it does:** for each source in `SOURCES` (`eia,noaa`), reads today's `reports/<source>/.../run_report.json` from S3, computes a 30-day skip history, and publishes **one** combined email to `zeus-dev-alerts` (succeeded/skipped counts + Snowflake rows loaded per source). It also reads `reports/dbt/.../run_report.json` and renders it as its own section (subject chip like `dbt 4 models / 14 tests` or `dbt FAILED 2 tests`, body lists failed-test names) — dbt is **not** a fan-out source, so `SOURCES` stays `eia,noaa`. A source or dbt run that wrote no report (it crashed → the execution-level alert already fired) is surfaced as "no report", not a crash. Adding a future source = append it to `var.sources`. Reuses `src/shared/report.py` (`format_digest`, `format_dbt_section`).

### Snowflake (live)

- **Shared:** warehouse `ZEUS_DEV_WH` (x-small, auto-suspend 60 min, auto-resume) in `infra/core/main.tf`; database `ZEUS_DEV` in `infra/core/snowflake.tf`.
- **Per-source landing stacks** are one `module "snowflake_landing"` call each (`snowflake_eia.tf`, `snowflake_noaa.tf`). The module derives every name from `source_name` and takes the table columns as a variable. Each call creates: a storage integration (`ZEUS_DEV_<SOURCE>_S3_INT`) + paired AWS IAM role (`zeus-dev-snowflake-<source>`), the schema + `<SOURCE>_GRID` table + `<SOURCE>_STAGE` over `curated/<source>/`, and a least-privilege key-pair loader (`ZEUS_DEV_<SOURCE>_LOADER`, USAGE + INSERT only).
  - **EIA:** `ZEUS_DEV.EIA.EIA_GRID`, stage `EIA_STAGE`. **NOAA:** `ZEUS_DEV.NOAA.NOAA_GRID`, stage `NOAA_STAGE`.
- **Load path:** the ingest Lambda's `COPY INTO ... FILE_FORMAT = (TYPE = PARQUET USE_LOGICAL_TYPE = TRUE) MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE`. **`USE_LOGICAL_TYPE = TRUE` is required** — without it EIA's `period` loads as a raw INT64 and NOAA's `date` as a raw INT32 ("Invalid date").
- **Auth:** public key in Terraform (`var.eia_loader_public_key` / `var.noaa_loader_public_key`), private key in SSM. Creating integrations + service users requires the `infra/core/` Snowflake provider to run as `ACCOUNTADMIN`.
- **Transformer (dbt):** account role `ZEUS_DEV_TRANSFORMER_ROLE` + key-pair service user `ZEUS_DEV_TRANSFORMER` (`infra/core/snowflake_transform.tf`) — read-only on the landing schemas, `CREATE SCHEMA` on `ZEUS_DEV`, owns the modeled schemas; rolled up to SYSADMIN. Same key contract as the loaders: public key in tfvars (`var.transformer_public_key`), private key in SSM (`/zeus/dev/snowflake/transformer_private_key`) for the dbt Lambda; manual local dbt runs keep using the gitignored `sf_transformer.p8`.
- **Table contract:** append-only landing; duplication from the lookback overlap is deduped downstream in dbt (EIA on `(period, respondent, fueltype)`, NOAA on `(date, station)`) keeping the latest `ingestion_date`. Per-file load metadata makes same-day re-runs idempotent.
- **Module note:** EIA's landing resources were originally inline in `snowflake_eia.tf`; they were moved into the module via `terraform state mv` (no destroy/recreate — the module reproduces every name/comment exactly). See README cross-cutting decision for the move list.

## Commands

```bash
# Python deps
uv sync --group dev
uv run pre-commit install
uv run pre-commit run --all-files

# Terraform — core (S3 + SNS + Snowflake warehouse/db + per-source landing stacks); apply this first.
# Requires in terraform.tfvars (gitignored): role = "ACCOUNTADMIN" (to create the
# storage integrations + service users) and one loader RSA public-key body per source
# (eia_loader_public_key, noaa_loader_public_key). Generate each key-pair once, e.g.:
#   openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out sf_noaa_loader.p8 -nocrypt
#   openssl rsa -in sf_noaa_loader.p8 -pubout -out sf_noaa_loader.pub   # paste body into tfvars
# (*.p8 and sf_*_loader.pub are gitignored.)
cd infra/core
terraform init
terraform plan
terraform apply
terraform output                          # bucket_name, bucket_arn, alerts_topic_arn, snowflake_warehouse_name, snowflake_account, snowflake_database_name

# Terraform — EIA pipeline
# The build step runs `uv pip install --python python3.12` via Terraform's /bin/sh
# provisioner, so uv AND python3.12 must be REAL binaries on PATH. If apply fails
# with "/bin/sh: uv: not found", see "Build troubleshooting" under Working norms.
cd infra/pipelines/eia
terraform init
terraform plan
terraform apply
terraform output                          # ingest_function_name, api_key_ssm_path

# Set the EIA API key in SSM (out-of-band, after first apply)
aws ssm put-parameter \
  --name /zeus/dev/eia/api_key \
  --value "$EIA_API_KEY" \
  --type SecureString \
  --overwrite

# Set the Snowflake loader private key in SSM (out-of-band, after pipeline apply)
aws ssm put-parameter \
  --name /zeus/dev/snowflake/eia_loader_private_key \
  --value "$(cat sf_eia_loader.p8)" \
  --type SecureString \
  --overwrite

# Smoke-test the Lambda for a couple of BAs
aws lambda invoke \
  --function-name zeus-dev-eia-ingest \
  --payload '{"units":["CISO","PJM"]}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/eia-out.json && cat /tmp/eia-out.json

# Trigger a full EIA run with the exact payload the state machine sends
aws lambda invoke \
  --function-name zeus-dev-eia-ingest \
  --payload "{\"units\": $(cd infra/pipelines/eia && terraform output -json balancing_authorities)}" \
  --cli-binary-format raw-in-base64-out \
  /tmp/eia-full.json && cat /tmp/eia-full.json

# Verify the day's raw partition landed
aws s3 ls "s3://zeus-dev-energy-data/raw/eia/ingestion_year=$(date -u +%Y)/ingestion_month=$(date -u +%m)/ingestion_day=$(date -u +%d)/" --recursive | wc -l
# expect 71 (minus any BAs skipped that day)

# Verify rows landed in Snowflake (Snowsight or any SQL client)
#   SELECT min(period), max(period), count(*) FROM ZEUS_DEV.EIA.EIA_GRID;

# Terraform — NOAA pipeline (same shape as EIA; uses the noaa loader key only)
cd infra/pipelines/noaa
terraform init && terraform apply
aws ssm put-parameter --name /zeus/dev/snowflake/noaa_loader_private_key \
  --value "$(cat sf_noaa_loader.p8)" --type SecureString --overwrite
aws lambda invoke --function-name zeus-dev-noaa-ingest \
  --payload '{"units":["CISO","PJM"]}' --cli-binary-format raw-in-base64-out \
  /tmp/noaa-out.json && cat /tmp/noaa-out.json
#   then: SELECT min(date), max(date), count(*) FROM ZEUS_DEV.NOAA.NOAA_GRID;

# Terraform — digest pipeline (one combined daily email across all sources)
cd infra/pipelines/digest
terraform init && terraform apply
aws lambda invoke --function-name zeus-dev-reports-digest \
  --payload '{}' --cli-binary-format raw-in-base64-out /tmp/digest-out.json && cat /tmp/digest-out.json

# Terraform — dbt runner (container image: Docker + aws CLI must be on PATH at apply
# time for the ECR login/build/push, like uv/python3.12 for the zip builds)
cd infra/pipelines/dbt
terraform init && terraform apply
aws ssm put-parameter --name /zeus/dev/snowflake/transformer_private_key \
  --value "$(cat sf_transformer.p8)" --type SecureString --overwrite
aws lambda invoke --function-name zeus-dev-dbt-run \
  --payload '{}' --cli-binary-format raw-in-base64-out --cli-read-timeout 320 \
  /tmp/dbt-out.json && cat /tmp/dbt-out.json
# expect {"status": "ok", "models_built": N, "tests_passed": N, "tests_failed": 0, ...}

# Terraform — orchestration (the daily state machine). Apply LAST: it consumes the
# eia/noaa/digest/dbt roots' outputs via remote state and fails to plan until they exist.
cd infra/pipelines/orchestration
terraform init && terraform apply

# Run the whole pipeline end-to-end (exactly what the daily cron does)
aws stepfunctions start-execution \
  --state-machine-arn "$(terraform output -raw state_machine_arn)" --input '{}'
aws stepfunctions describe-execution --execution-arn <arn from above>
# expect status SUCCEEDED; output shows ingest [{failed:false}, ...] + dbt + digest payloads

# One-time historical backfill — two phases (fetch→raw→curated) then a whole-stage COPY.
# Idempotent + resume-safe (skips already-written days). Run ONCE per source; do not
# re-run months later (Snowflake load metadata expires after 64 days → duplicate loads).
# Adding BAs to an already-backfilled source: re-run extract+transform with --overwrite
# (rewrites raw + curated so the day files include the new BAs); the whole-stage COPY then
# reloads everything → the original BAs land as duplicates, deduped downstream in dbt.
# NOTE: backfill/noaa/run.py forces IPv4. The dev machine's IPv6 route to NCEI is broken,
# and urllib3 prefers IPv6 → ~10 min/request stall without it (a single request is ~2 s on
# IPv4). NCEI can still 429 under heavy concurrency; the fetch retry wrapper backs off.
uv run python backfill/eia/run.py  extract   --start 2017-01-01       # then: transform, then snowflake_load.py
uv run python backfill/noaa/run.py extract   --start 2010-01-01       # one batched request per BA-year
uv run python backfill/noaa/run.py transform --start 2010-01-01       # per-day curated Parquet (S3-only, parallel)
SNOWFLAKE_ACCOUNT=<org-account> SNOWFLAKE_PRIVATE_KEY_FILE=sf_noaa_loader.p8 \
  uv run python backfill/noaa/snowflake_load.py                       # whole-stage COPY into NOAA_GRID
```

## Pre-commit hooks

- **gitleaks** — scans for secrets before every commit. Commits will be blocked if secrets are detected.

## Decisions reference

`README.md` records the architectural decisions for the platform — cross-cutting decisions (region, Terraform structure, S3 layout, secrets, packaging, observability, shared helpers) at the top, pipeline-specific decisions in their own sections. Modeling and business-rule decisions for the dbt layer (grain, metric definitions, join semantics) live in `transform/DECISIONS.md` (`M-N` entries, same format); the enforceable contract (tests, column docs) stays in each layer's `schema.yml`. Read the relevant file before proposing structural or modeling changes.

## Working norms

### Code & infra
- Lambda source lives in `src/lambdas/<source>/ingest/`. `handler.py` is thin orchestration; the only genuinely source-specific modules are `client.py` (HTTP client) and `schema.py` (pyarrow schema + normalization). Everything else is shared in `src/shared/` (`paths`, `s3_io`, `ssm`, `sns`, `snowflake_io`, `time_window`, `report`, **`ingest`**) and copied into the build dir at the zip root, so handlers import them as `from shared import …`. `ingest.run_ingest(...)` is the shared daily orchestrator (source-specific pieces injected); `report.py` is source-agnostic (takes `source` as a param) and used by both ingest Lambdas and the digest. `snowflake_io` owns the key-pair connection (`copy_into`) and the `COPY INTO` statement builder (`copy_statement`); the daily handlers build SQL via the latter (the backfills keep their own inline statement).
- Per-source `handler.py` is a thin shim (~12 lines) over the shared `src/shared/ingest.py` orchestrator: it builds `ingest.config_from_env()` and calls `ingest.run_ingest(...)`, injecting the only two things that differ per source — the `fetch_fn` (EIA wraps its api-keyed client in a closure to match the `(unit, start, end)` contract) and the `window_fn` (`lookback_window` hourly vs `lookback_window_dates` daily) — plus the source's `schema`/`normalize_row`. The fan-out → consolidate → COPY → report orchestration lives once in `ingest.run_ingest`.
- Build artifacts are named `<prefix>-<source>-ingest.zip` (e.g. `zeus-dev-eia-ingest.zip`) / `<prefix>-reports-digest.zip` under `infra/build/`. Each zip contains: pip-installed deps + the handler dir's `*.py` + `src/shared/`. The dbt runner ships as a **container image** instead (ECR repo `zeus-dev-dbt-run`, tag = content hash over Dockerfile/runner `*.py`/`transform/`/`src/shared/`; docker build context = repo root, allowlisted by the repo-root `.dockerignore`) because `dbt-snowflake` doesn't fit the zip limits.
- Each pipeline (`eia`, `noaa`, `digest`, `dbt`) is its own self-contained Terraform root under `infra/pipelines/<name>/`: `remote_state.tf` consumes `infra/core` outputs (bucket, alerts topic, snowflake account/warehouse/db), `locals.tf` holds the unit list, and `main.tf` authors any SSM `SecureString` plus the Lambda — zip roots call `module "lambda_job"` once; the dbt root authors an ECR repo + fingerprinted docker build/push + an inline `package_type = "Image"` Lambda (`lambda_job` is zip-only by design). **Pipeline roots own no triggers or failure routing** — both live in `infra/pipelines/orchestration/`, whose state machine consumes each root's `*_function_arn` (and `balancing_authorities` for the ingests) outputs via remote state. Apply order: core → pipeline roots → orchestration last. Two shared modules: `infra/modules/lambda_job/` (zip Lambda packaging + IAM) and `infra/modules/snowflake_landing/` (one source's Snowflake landing stack, consumed by `infra/core`, names derived from `source_name`).
- Historical backfills (`backfill/<source>/`) mirror the daily pipeline's S3 layout: a two-phase `run.py extract|transform` writes one raw JSON per `(unit, observation-day)` and one curated Parquet per observation-day (partition **backdated** to the observation date, `ingestion_date` = that day), then `snowflake_load.py` whole-stage COPYs. Both phases skip already-written keys for idempotent resume, or take `--overwrite` to re-write them (used to add BAs to an already-backfilled range — see the backfill note in Commands). `backfill/noaa/run.py` forces IPv4 because the dev machine's IPv6 route to NCEI is broken (urllib3 prefers IPv6 → ~10 min/request stall otherwise). Backfill scripts may repeat patterns rather than share code with the Lambda — they reuse `src/` modules via `_bootstrap.py` (sets `sys.path`).
- Naming convention: `${project}-${env}-<source>-<resource>` (e.g. `zeus-dev-eia-ingest`). Resource names are derived in the pipeline root from `local.prefix` + the source name.
- SSM paths follow `/${project}/${env}/<source>/<name>` (e.g. `/zeus/dev/eia/api_key`, `/zeus/dev/snowflake/eia_loader_private_key`), constructed in the pipeline root.
- Secrets never in code, never in `terraform.tfvars`, never in `terraform.tfstate`. SSM `SecureString` with `lifecycle.ignore_changes = [value]`; rotate via `aws ssm put-parameter`. Snowflake auth follows this too: only each loader's **public** key is in Terraform (`var.eia_loader_public_key` / `var.noaa_loader_public_key`); the private keys live in SSM. The private-key files (`*.p8`) and `sf_*_loader.pub` are gitignored.
- Lambda packaging is ZIP built locally via `uv pip install --python python3.12 --target …`, then **uploaded to S3** and referenced by `s3_bucket`/`s3_key` (the package is ~49 MiB zipped, over the 50 MiB direct-upload limit). Don't rely on the project venv for the build; `unset VIRTUAL_ENV` first. `uv` and `python3.12` must be on PATH when running `terraform apply`. The `null_resource.build` trigger fingerprints `requirements.txt` + `**/*.py` under both the Lambda's `src_dir` and `src/shared/`, so any change forces a rebuild. `snowflake-connector-python` pins `cryptography`/`pyOpenSSL` (loose upstream bounds otherwise resolve to an import-incompatible pair).
- Event payload contract: the state machine invokes each ingest Lambda with `{"units": [...]}` (baked into the ASL from the pipeline roots' `balancing_authorities` outputs); the handler reads `event["units"]`. The same contract works for any fan-out source. The dbt and digest steps take `{}`.
- Shared AWS clients (`src/shared/{s3_io,ssm,sns,snowflake_io}.py`) are module-level singletons by convention; tests substitute them by monkeypatching module attributes (see the dbt container test harness). Keep new shared helpers consistent.

### CI (GitHub Actions)
- **Phase 1 (live):** `.github/workflows/ci.yml` runs the offline gates on every PR and push to main — pre-commit (gitleaks), `dbt parse` (dummy creds; parse doesn't connect), and per-root `terraform fmt -check` + `validate` (`init -backend=false` — no state, no cloud creds). It automates the cheap end of "Done means"; the warehouse- and AWS-touching tiers stay local for now.
- **Phase 2 (planned — dbt clone CI):** on PR, `CREATE DATABASE ZEUS_CI_PR_<n> CLONE ZEUS_DEV` (zero-copy, instant) → `dbt build` against the clone (`SNOWFLAKE_DATABASE` env var — profiles.yml is already env-var driven, zero code change) → drop the clone. Prereqs: a least-privilege CI Snowflake service user + role (same key-pair pattern as the loaders, provisioned in `infra/core`), private key in GitHub Actions secrets. Upgrade path: Slim CI (`state:modified+`) once a production manifest is stored. **Trigger to build it:** the marts layer landing (incremental tables make clone isolation actually matter; views barely need it).
- **Phase 3 (deferred — Terraform plan on PRs):** blocked on migrating Terraform state from local to an S3 backend (+ DynamoDB locking) and AWS OIDC for the runner; the `local-exec` builds also need docker/uv/python3.12 on the runner. Do the backend migration as its own project first; don't bolt it onto CI.
- **`/bin/sh: uv: not found` during `terraform apply`.** The Lambda build is a `local-exec` provisioner (`infra/modules/lambda_job/main.tf`) that Terraform runs under **`/bin/sh`** — a non-interactive, non-login shell that does **not** source your shell config. If `uv` is a shim/alias/shell function, or lives only on your interactive shell's PATH (common with version managers or a venv that doesn't ship `uv`), the build fails with `/bin/sh: uv: not found` **even though `uv` works fine in your terminal**. The fix is to put a **real `uv` binary** on a directory that's already on PATH for non-interactive shells — `~/.local/bin` works:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh   # installs uv + uvx to ~/.local/bin
  uv --version                                       # must resolve as a plain binary, not a shim
  ```
  Then re-run `terraform apply` (a half-failed apply just continues — it recreates the build and the remaining resources). `python3.12` must likewise be a real binary on PATH; the project's `.venv/bin/python3.12` satisfies that with the venv active. The build does `unset VIRTUAL_ENV` itself, so don't rely on the venv providing `uv`.
- **dbt root: Docker + ECR auth at apply time.** The dbt root's `null_resource.build` runs `aws ecr get-login-password | docker login` then `docker build --platform linux/amd64` + `docker push` under the same `/bin/sh` provisioner constraints — `docker` and `aws` must be real binaries on PATH. A zip-root apply may also fail once with `Provider produced inconsistent final plan` when the zip is rebuilt mid-apply (the archive hash changes between plan and apply); the immediate re-apply converges.

### S3 layout
- Two layers: `raw/<source>/` (one JSON file per atomic unit, e.g. per BA) and `curated/<source>/` (one consolidated Parquet per day). A `reports/<source>/` prefix holds per-run JSON reports.
- Multi-level Hive partitioning on date for all layers (`ingestion_year=/ingestion_month=/ingestion_day=/`).
- Raw filename = `<unit>.json`. No `<unit>=` partition in the path — the unit is in the data and the filename.
- Curated filename = `<source>_grid.parquet` (e.g. `eia_grid.parquet`). Snappy compression.
- Append-only, immutable. Downstream de-dup at query time, not write time.

### Done means

Verification is proportional to the **blast radius of the change** — match the test to what the change can actually break, not to the size of the pipeline.

- Terraform `validate` passes and `plan` is clean.
- **dbt models/tests only (`transform/`):** local `dbt build` green (models + tests) + a spot-check query of the changed columns + `terraform apply` the dbt root + one `aws lambda invoke zeus-dev-dbt-run` to validate the deployed image (~23 s, dbt only). **No state-machine run, no re-ingestion** — a model change can't break ingestion or wiring, and a full DAG run can fail for reasons unrelated to the change. The apply is NOT optional: the dbt project is **baked into the image**, so a local-only build is silently reverted by the next cron.
- **dbt runner changes (handler / Dockerfile / deps / mp patches):** local container gate (run the handler in the image with `_multiprocessing.SemLock` stubbed to raise) + apply + one dbt Lambda invoke.
- **Ingestion Lambda changes:** smoke-test one invocation end-to-end (Lambda → S3 → Snowflake) before declaring done; for pipeline changes, invoke once with the full units list and confirm all expected raw + curated partitions land.
- **Orchestration changes:** one `aws stepfunctions start-execution` end-to-end — the wiring itself is under test: all states green, the digest email arrives (with the dbt section), and a deliberately failed step still runs the digest + fires the SNS alert + marks the execution Failed.
- For Snowflake-touching changes: confirm rows land in the source's landing table (`ZEUS_DEV.EIA.EIA_GRID` / `ZEUS_DEV.NOAA.NOAA_GRID`) with real timestamps/dates (`period` / `date`), not "Invalid date".
- No hardcoded values unless explicitly agreed.

## Tech debt

Known issues to fix later. Not blocking; documented here so they aren't lost.

- **Lambda IAM scaffolding duplicated.** `infra/modules/lambda_job/iam.tf` and the dbt root (`infra/pipelines/dbt/main.tf`) each define the role + basic-execution attachment + inline-policy trio. Two copies is acceptable; **the third copy (next container-image Lambda) is the trigger** to extract a shared `lambda_iam` module (use `moved` blocks — no role recreation).
