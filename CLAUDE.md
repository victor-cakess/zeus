# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python 3.12+ project managed with `uv`. The repo is a data platform for energy data ingestion and modeling. Two ingestion pipelines land data in S3 and load it into Snowflake landing tables: **EIA** (hourly fuel-type → `ZEUS_DEV.EIA.EIA_GRID`) and **NOAA** (daily weather summaries → `ZEUS_DEV.NOAA.NOAA_GRID`). A third Lambda (`zeus-dev-reports-digest`) emails one combined daily run-report covering both sources. The two sources are deliberately keyed on the same balancing-authority codes (`ba`) so weather joins to grid data downstream.

## Repository layout

```
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
    eia/                          # EIA pipeline root: SSM (api key + snowflake key) + lambda_job + EventBridge + on-failure config
    noaa/                         # NOAA pipeline root: SSM (snowflake key only — NCEI needs no api key) + lambda_job + EventBridge + on-failure config
    digest/                       # digest pipeline root: lambda_job + daily EventBridge (reads all sources' run reports, emails one summary)
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
    report.py                     # source-agnostic run-report build + per-source email + format_digest + skip history
  lambdas/
    eia/ingest/                   # EIA Lambda: handler.py (orchestration) + client.py (paginated HTTP) + schema.py (dash→underscore) + requirements.txt
    noaa/ingest/                  # NOAA Lambda: handler.py + client.py (NCEI daily-summaries, batched stations, no token) + schema.py (wide, 13 datatypes) + requirements.txt
    digest/                       # digest Lambda: handler.py reads each source's run_report.json, sends one combined email (requirements.txt: boto3 only)
extraction/                       # gitignored, exploratory notebooks
backfill/                         # one-off historical backfill scripts (reuse src/ via _bootstrap.py); see backfill/README.md
  eia/                            # fetch→raw→curated→COPY: run.py (extract/transform) + fetch.py + extract.py + transform.py + snowflake_load.py + units.py (BA list) + _bootstrap.py + logconf.py
  noaa/                           # same two-phase pattern (run.py/fetch/extract/transform/snowflake_load + _bootstrap + logconf; stations from src client, no units.py); day-partitioned, backdated, idempotent resume
README.md                         # project overview + architectural decisions
```

## Pipelines currently live

### EIA daily ingestion (production)

- **Trigger:** EventBridge rule `zeus-dev-eia-daily` fires `cron(0 7 * * ? *)` (07:00 UTC = 04:00 sa-east-1). It invokes the Lambda **directly and asynchronously** with the payload `{"units": [...]}` (the full BA list from `infra/pipelines/eia/locals.tf`).
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
- **Alerting:** the Lambda's async **on-failure destination** points at the shared SNS topic `zeus-dev-alerts`. EventBridge invokes asynchronously with `maximum_retry_attempts = 0`, so any unhandled crash (OOM, timeout, init error) or the total-outage `ValueError` routes the failed event to the topic. (Success-path summaries come from the digest, not per-pipeline.)
- **Observed performance:** 71 BAs in ~12.5 s, peak memory ~247 MB.

### NOAA daily ingestion (production)

- **Trigger:** EventBridge rule `zeus-dev-noaa-daily` fires `cron(30 7 * * ? *)` (staggered 30 min after EIA). It invokes Lambda `zeus-dev-noaa-ingest` directly and asynchronously with `{"units": [...]}` — the 14-BA list (`CISO, PJM, ERCO, MISO, ISNE, NYIS, SWPP, TVA, SOCO, DUK, FPL, BPAT, PSCO, SRP`) from `infra/pipelines/noaa/locals.tf`.
- **Worker:** single Lambda (Python 3.12, 1024 MB, 300 s, deployed via S3). Same orchestration shape as EIA: fan out per-BA fetch → write one raw JSON per BA → consolidate the day's partition to one curated Parquet → `COPY INTO ZEUS_DEV.NOAA.NOAA_GRID` → write `run_report.json`. No API key (NCEI `daily-summaries` needs none). `lookback_window_dates` gives a rolling 7-day date window (NOAA data lags a few days, so the lookback catches late/QC-revised days).
- **Fan-out unit = BA.** Each of the 14 BAs maps to 3–10 weather stations (the `STATIONS` map in `src/lambdas/noaa/ingest/client.py`); the client fetches all of a BA's stations in **one batched NCEI request** (comma-separated `stations=`) and tags every row with its `ba`. Raw layout is one `<ba>.json` per BA, mirroring EIA.
- **Grain / schema:** one row per `(ba, station, date)`, **wide** — 13 datatypes (`TMAX, TMIN, TAVG, PRCP, SNOW, SNWD, AWND, WSF2, WSF5, WDF2, RHAV, ASLP, ADPT`), metric units, plus `ingestion_date`. Absent datatypes land null.
- **Secrets:** Snowflake loader key only, SSM `/zeus/dev/snowflake/noaa_loader_private_key`. No api-key parameter.
- **Outputs:** `raw/noaa/.../<ba>.json`, `curated/noaa/.../noaa_grid.parquet`, `reports/noaa/.../run_report.json` — same partition scheme as EIA.

### Daily digest (production)

- **Trigger:** EventBridge rule `zeus-dev-reports-digest-daily` fires `cron(0 8 * * ? *)` (after both ingest runs). Invokes `zeus-dev-reports-digest` (256 MB, 60 s) with no payload.
- **What it does:** for each source in `SOURCES` (`eia,noaa`), reads today's `reports/<source>/.../run_report.json` from S3, computes a 30-day skip history, and publishes **one** combined email to `zeus-dev-alerts` (succeeded/skipped counts + Snowflake rows loaded per source). A source that wrote no report (it crashed → its own on-failure alert already fired) is surfaced as "no report", not a crash. Adding a future source = append it to `var.sources`. Reuses `src/shared/report.py` (`format_digest`).

### Snowflake (live)

- **Shared:** warehouse `ZEUS_DEV_WH` (x-small, auto-suspend 60 min, auto-resume) in `infra/core/main.tf`; database `ZEUS_DEV` in `infra/core/snowflake.tf`.
- **Per-source landing stacks** are one `module "snowflake_landing"` call each (`snowflake_eia.tf`, `snowflake_noaa.tf`). The module derives every name from `source_name` and takes the table columns as a variable. Each call creates: a storage integration (`ZEUS_DEV_<SOURCE>_S3_INT`) + paired AWS IAM role (`zeus-dev-snowflake-<source>`), the schema + `<SOURCE>_GRID` table + `<SOURCE>_STAGE` over `curated/<source>/`, and a least-privilege key-pair loader (`ZEUS_DEV_<SOURCE>_LOADER`, USAGE + INSERT only).
  - **EIA:** `ZEUS_DEV.EIA.EIA_GRID`, stage `EIA_STAGE`. **NOAA:** `ZEUS_DEV.NOAA.NOAA_GRID`, stage `NOAA_STAGE`.
- **Load path:** the ingest Lambda's `COPY INTO ... FILE_FORMAT = (TYPE = PARQUET USE_LOGICAL_TYPE = TRUE) MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE`. **`USE_LOGICAL_TYPE = TRUE` is required** — without it EIA's `period` loads as a raw INT64 and NOAA's `date` as a raw INT32 ("Invalid date").
- **Auth:** public key in Terraform (`var.eia_loader_public_key` / `var.noaa_loader_public_key`), private key in SSM. Creating integrations + service users requires the `infra/core/` Snowflake provider to run as `ACCOUNTADMIN`.
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

# Trigger a full run with the exact payload EventBridge sends
aws lambda invoke \
  --function-name zeus-dev-eia-ingest \
  --payload "$(aws events list-targets-by-rule --rule zeus-dev-eia-daily --query 'Targets[0].Input' --output text)" \
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

`README.md` records the architectural decisions for the platform — cross-cutting decisions (region, Terraform structure, S3 layout, secrets, packaging, observability, shared helpers) at the top, EIA-specific decisions in their own section. Read it before proposing structural changes.

## Working norms

### Code & infra
- Lambda source lives in `src/lambdas/<source>/ingest/`. `handler.py` is thin orchestration; the only genuinely source-specific modules are `client.py` (HTTP client) and `schema.py` (pyarrow schema + normalization). Everything else is shared in `src/shared/` (`paths`, `s3_io`, `ssm`, `sns`, `snowflake_io`, `time_window`, `report`, **`ingest`**) and copied into the build dir at the zip root, so handlers import them as `from shared import …`. `ingest.run_ingest(...)` is the shared daily orchestrator (source-specific pieces injected); `report.py` is source-agnostic (takes `source` as a param) and used by both ingest Lambdas and the digest. `snowflake_io` owns the key-pair connection (`copy_into`) and the `COPY INTO` statement builder (`copy_statement`); the daily handlers build SQL via the latter (the backfills keep their own inline statement).
- Per-source `handler.py` is a thin shim (~12 lines) over the shared `src/shared/ingest.py` orchestrator: it builds `ingest.config_from_env()` and calls `ingest.run_ingest(...)`, injecting the only two things that differ per source — the `fetch_fn` (EIA wraps its api-keyed client in a closure to match the `(unit, start, end)` contract) and the `window_fn` (`lookback_window` hourly vs `lookback_window_dates` daily) — plus the source's `schema`/`normalize_row`. The fan-out → consolidate → COPY → report orchestration lives once in `ingest.run_ingest`.
- Build artifacts are named `<prefix>-<source>-ingest.zip` (e.g. `zeus-dev-eia-ingest.zip`) / `<prefix>-reports-digest.zip` under `infra/build/`. Each zip contains: pip-installed deps + the handler dir's `*.py` + `src/shared/`.
- Each pipeline (`eia`, `noaa`, `digest`) is its own self-contained Terraform root under `infra/pipelines/<name>/`: `remote_state.tf` consumes `infra/core` outputs (bucket, alerts topic, snowflake account/warehouse/db), `locals.tf` holds the unit list, and `main.tf` authors any SSM `SecureString`, calls `module "lambda_job"` once, and wires the EventBridge rule, `aws_lambda_permission`, and async `aws_lambda_function_event_invoke_config` (on-failure → SNS). Two shared modules: `infra/modules/lambda_job/` (Lambda packaging + IAM) and `infra/modules/snowflake_landing/` (one source's Snowflake landing stack, consumed by `infra/core`, names derived from `source_name`).
- Historical backfills (`backfill/<source>/`) mirror the daily pipeline's S3 layout: a two-phase `run.py extract|transform` writes one raw JSON per `(unit, observation-day)` and one curated Parquet per observation-day (partition **backdated** to the observation date, `ingestion_date` = that day), then `snowflake_load.py` whole-stage COPYs. Both phases skip already-written keys for idempotent resume, or take `--overwrite` to re-write them (used to add BAs to an already-backfilled range — see the backfill note in Commands). `backfill/noaa/run.py` forces IPv4 because the dev machine's IPv6 route to NCEI is broken (urllib3 prefers IPv6 → ~10 min/request stall otherwise). Backfill scripts may repeat patterns rather than share code with the Lambda — they reuse `src/` modules via `_bootstrap.py` (sets `sys.path`).
- Naming convention: `${project}-${env}-<source>-<resource>` (e.g. `zeus-dev-eia-ingest`). Resource names are derived in the pipeline root from `local.prefix` + the source name.
- SSM paths follow `/${project}/${env}/<source>/<name>` (e.g. `/zeus/dev/eia/api_key`, `/zeus/dev/snowflake/eia_loader_private_key`), constructed in the pipeline root.
- Secrets never in code, never in `terraform.tfvars`, never in `terraform.tfstate`. SSM `SecureString` with `lifecycle.ignore_changes = [value]`; rotate via `aws ssm put-parameter`. Snowflake auth follows this too: only each loader's **public** key is in Terraform (`var.eia_loader_public_key` / `var.noaa_loader_public_key`); the private keys live in SSM. The private-key files (`*.p8`) and `sf_*_loader.pub` are gitignored.
- Lambda packaging is ZIP built locally via `uv pip install --python python3.12 --target …`, then **uploaded to S3** and referenced by `s3_bucket`/`s3_key` (the package is ~49 MiB zipped, over the 50 MiB direct-upload limit). Don't rely on the project venv for the build; `unset VIRTUAL_ENV` first. `uv` and `python3.12` must be on PATH when running `terraform apply`. The `null_resource.build` trigger fingerprints `requirements.txt` + `**/*.py` under both the Lambda's `src_dir` and `src/shared/`, so any change forces a rebuild. `snowflake-connector-python` pins `cryptography`/`pyOpenSSL` (loose upstream bounds otherwise resolve to an import-incompatible pair).
- Event payload contract: EventBridge invokes the Lambda directly with `{"units": [...]}`; the handler reads `event["units"]`. The same contract works for any fan-out source.

### Build troubleshooting
- **`/bin/sh: uv: not found` during `terraform apply`.** The Lambda build is a `local-exec` provisioner (`infra/modules/lambda_job/main.tf`) that Terraform runs under **`/bin/sh`** — a non-interactive, non-login shell that does **not** source your shell config. If `uv` is a shim/alias/shell function, or lives only on your interactive shell's PATH (common with version managers or a venv that doesn't ship `uv`), the build fails with `/bin/sh: uv: not found` **even though `uv` works fine in your terminal**. The fix is to put a **real `uv` binary** on a directory that's already on PATH for non-interactive shells — `~/.local/bin` works:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh   # installs uv + uvx to ~/.local/bin
  uv --version                                       # must resolve as a plain binary, not a shim
  ```
  Then re-run `terraform apply` (a half-failed apply just continues — it recreates the build and the remaining resources). `python3.12` must likewise be a real binary on PATH; the project's `.venv/bin/python3.12` satisfies that with the venv active. The build does `unset VIRTUAL_ENV` itself, so don't rely on the venv providing `uv`.

### S3 layout
- Two layers: `raw/<source>/` (one JSON file per atomic unit, e.g. per BA) and `curated/<source>/` (one consolidated Parquet per day). A `reports/<source>/` prefix holds per-run JSON reports.
- Multi-level Hive partitioning on date for all layers (`ingestion_year=/ingestion_month=/ingestion_day=/`).
- Raw filename = `<unit>.json`. No `<unit>=` partition in the path — the unit is in the data and the filename.
- Curated filename = `<source>_grid.parquet` (e.g. `eia_grid.parquet`). Snappy compression.
- Append-only, immutable. Downstream de-dup at query time, not write time.

### Done means
- Terraform `validate` passes and `plan` is clean.
- For Lambda changes: smoke-test one invocation end-to-end (Lambda → S3 → Snowflake) before declaring done.
- For pipeline changes: invoke the Lambda once with the full units list and confirm all expected raw + curated partitions land.
- For Snowflake-touching changes: confirm rows land in the source's landing table (`ZEUS_DEV.EIA.EIA_GRID` / `ZEUS_DEV.NOAA.NOAA_GRID`) with real timestamps/dates (`period` / `date`), not "Invalid date".
- No hardcoded values unless explicitly agreed.

## Tech debt

Known issues to fix later. Not blocking; documented here so they aren't lost.

- **Duplicate NOAA station ID `USW00014733` across two BAs.** In `src/lambdas/noaa/ingest/client.py`'s `STATIONS` map, the same GHCND id `USW00014733` is listed under both PJM (line 30, labeled "Baltimore" — correct; this is Baltimore-Washington Intl / BWI) and MISO (line 53, labeled "Indianapolis" — **wrong id**). So MISO silently ingests Baltimore's weather tagged as Indianapolis, and the 40-station map has only 39 unique stations. It does **not** corrupt the grain (rows are keyed `(ba, station, date)` and `ba` is tagged client-side), so MISO and PJM each get a valid row for that station — but one of MISO's 10 stations is geographically wrong. **Fix:** replace MISO's `USW00014733` with the real Indianapolis Intl id (`USW00093819`), then backfill/re-ingest MISO so the corrected station's history lands. Until then, treat MISO's "Indianapolis" weather as duplicate Baltimore data.
