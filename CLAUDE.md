# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python 3.12+ project managed with `uv`. The repo is a data platform for energy data ingestion and modeling. It has one production pipeline (EIA hourly fuel-type ingestion) that lands data in S3 and loads it into a Snowflake landing table (`ZEUS_DEV.EIA.EIA_GRID`).

## Repository layout

```
infra/
  core/                           # Terraform root: shared S3 bucket + SNS alerts topic + Snowflake warehouse + EIA Snowflake DDL
    backend.tf, providers.tf, variables.tf, locals.tf, main.tf, sns.tf, outputs.tf
    snowflake_eia.tf              # storage integration + IAM trust + ZEUS_DEV.EIA DDL (table/stage) + key-pair loader
  modules/
    lambda_job/                   # one Lambda + IAM role + ZIP-via-S3 packaging (the only shared module)
  pipelines/
    eia/                          # pipeline root: authors its own resources, calls lambda_job once
      backend.tf, providers.tf, variables.tf, locals.tf
      remote_state.tf             # consumes infra/core outputs (bucket, alerts topic, snowflake account/db)
      main.tf                     # SSM params (api key + snowflake key) + module "lambda_job" + EventBridge rule + on-failure config
      outputs.tf
  build/                          # gitignored — Lambda zip artifacts (one dir + zip per Lambda)
src/
  shared/                         # importable by every Lambda; copied into each build at zip root
    paths.py                      # raw_key / raw_prefix / curated_prefix / report_key — single S3-layout owner
    s3_io.py                      # put_json / put_bytes / get_json / list_keys / iter_objects
    ssm.py                        # cached get_parameter
    sns.py                        # publish(topic_arn, subject, message)
    snowflake_io.py               # copy_into(...) — key-pair connection, run a statement, return rows loaded
    time_window.py                # today_utc / lookback_window
  lambdas/
    eia/
      ingest/                     # single Lambda for the whole pipeline (fan-out fetch → consolidate → Snowflake)
        handler.py                # thin orchestration: fan-out → consolidate → COPY INTO Snowflake → report
        client.py                 # EIA-specific HTTP client (pagination + retry)
        schema.py                 # pyarrow schema + dash→underscore row normalization
        report.py                 # run-report build + email formatting + skip history
        requirements.txt          # runtime deps (`requests`, `pyarrow`, `snowflake-connector-python` + pinned `cryptography`/`pyOpenSSL`)
extraction/                       # gitignored, exploratory notebooks
backfill/                         # one-off historical backfill scripts (reuse src/ via _bootstrap.py)
  eia/snowflake_load.py           # one-time whole-stage COPY INTO of all historical curated Parquet
README.md                         # project overview + architectural decisions
```

## Pipelines currently live

### EIA daily ingestion (production)

- **Trigger:** EventBridge rule `zeus-dev-eia-daily` fires `cron(0 7 * * ? *)` (07:00 UTC = 04:00 sa-east-1). It invokes the Lambda **directly and asynchronously** with the payload `{"units": [...]}` (the full BA list from `infra/pipelines/eia/locals.tf`).
- **Worker:** a single Lambda `zeus-dev-eia-ingest` (Python 3.12, 1024 MB, 300 s timeout, deployed via S3). One invocation does the whole run:
  1. Fans out the per-BA fetch across a `ThreadPoolExecutor` (`MAX_WORKERS=20`), writing one raw JSON file per BA.
  2. Reads back the day's raw partition and consolidates every row into one Snappy-compressed Parquet in the curated layer.
  3. Runs `COPY INTO ZEUS_DEV.EIA.EIA_GRID` from the external stage to load that day's Parquet into Snowflake (Snowflake reads S3 directly via the storage integration).
  4. Writes a run report to the reports layer and emails a summary (including a Snowflake `loaded N rows` line).
- **Active units:** 71 balancing authorities in `infra/pipelines/eia/locals.tf`.
- **Secrets:** EIA API key in SSM `/zeus/dev/eia/api_key`; Snowflake loader private key in SSM `/zeus/dev/snowflake/eia_loader_private_key` (both `SecureString`). Lambda role has scoped `ssm:GetParameter` + `kms:Decrypt` on both. Each is fetched per run before it's needed.
- **Raw output:** `s3://zeus-dev-energy-data/raw/eia/ingestion_year=YYYY/ingestion_month=MM/ingestion_day=DD/<unit>.json`. Append-only, immutable; rolling 7-day lookback per run. Overlapping windows are intentional and de-duplicated downstream at query time on `(period, respondent, fueltype)`.
- **Curated output:** `s3://zeus-dev-energy-data/curated/eia/ingestion_year=YYYY/ingestion_month=MM/ingestion_day=DD/eia_grid.parquet`. Single Snappy-compressed Parquet per day, produced by the ingest Lambda from that day's raw files.
- **Reports output:** `s3://zeus-dev-energy-data/reports/eia/.../run_report.json` — one per run, listing succeeded and skipped units (with reasons).
- **Per-BA fault tolerance:** a BA that errors or returns 0 rows is recorded as `skipped` and the run continues, consolidating whatever landed. Only a **total outage** (0 rows consolidated) raises `ValueError` and fails the invocation.
- **Reporting & alerting (both target the shared SNS topic `zeus-dev-alerts`):**
  - **Run report email** — published by the Lambda on every completed run: succeeded/skipped counts, per-skip reasons, and a 30-day skip-frequency history.
  - **Failure alert** — the Lambda's async **on-failure destination** points at the topic. Because EventBridge invokes asynchronously with `maximum_retry_attempts = 0`, any unhandled crash (OOM, timeout, init error) or the total-outage `ValueError` routes the failed event to the topic.
- **Observed performance:** 71 BAs in ~12.5 s, peak memory ~247 MB.

### Snowflake (EIA landing table — live)

- **Objects** (all in `infra/core/snowflake_eia.tf`): warehouse `ZEUS_DEV_WH` (x-small, auto-suspend 60 min, auto-resume), database `ZEUS_DEV`, schema `EIA`, table `ZEUS_DEV.EIA.EIA_GRID`.
- **Load path:** external stage `EIA_STAGE` over `s3://zeus-dev-energy-data/curated/eia/` + storage integration `ZEUS_DEV_EIA_S3_INT` (paired AWS IAM role `zeus-dev-snowflake-eia`). The ingest Lambda's `COPY INTO ... FILE_FORMAT = (TYPE = PARQUET USE_LOGICAL_TYPE = TRUE)` loads each day's partition. **`USE_LOGICAL_TYPE = TRUE` is required** — without it `period` loads as a raw INT64 ("Invalid date").
- **Auth:** least-privilege key-pair service user `ZEUS_DEV_EIA_LOADER` (USAGE + INSERT only). Public key in Terraform (`var.eia_loader_public_key`), private key in SSM. Creating the integration + user requires the `infra/core/` Snowflake provider to run as `ACCOUNTADMIN`.
- **Table contract:** append-only landing; ~7× duplication from the 7-day lookback overlap, deduped downstream in dbt on `(period, respondent, fueltype)` keeping the latest `ingestion_date`. Per-file load metadata makes same-day re-runs idempotent.
- **Backfill:** `backfill/eia/snowflake_load.py` is a one-time whole-stage COPY of all historical curated Parquet — do **not** re-run (load metadata expires after 64 days, re-loading old files as duplicates).

## Commands

```bash
# Python deps
uv sync --group dev
uv run pre-commit install
uv run pre-commit run --all-files

# Terraform — core (S3 + SNS + Snowflake warehouse + EIA Snowflake DDL); apply this first.
# Requires in terraform.tfvars (gitignored): role = "ACCOUNTADMIN" (to create the
# storage integration + service user) and eia_loader_public_key (the loader RSA
# public key body). Generate the key-pair once:
#   openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out sf_eia_loader.p8 -nocrypt
#   openssl rsa -in sf_eia_loader.p8 -pubout -out sf_eia_loader.pub   # paste body into tfvars
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

# One-time backfill: load all historical curated Parquet into Snowflake (do not re-run)
SNOWFLAKE_ACCOUNT=<org-account> SNOWFLAKE_PRIVATE_KEY_FILE=sf_eia_loader.p8 \
  uv run python backfill/eia/snowflake_load.py
```

## Pre-commit hooks

- **gitleaks** — scans for secrets before every commit. Commits will be blocked if secrets are detected.

## Decisions reference

`README.md` records the architectural decisions for the platform — cross-cutting decisions (region, Terraform structure, S3 layout, secrets, packaging, observability, shared helpers) at the top, EIA-specific decisions in their own section. Read it before proposing structural changes.

## Working norms

### Code & infra
- Lambda source lives in `src/lambdas/<source>/ingest/`. `handler.py` is thin orchestration; source-specific logic sits in sibling modules (`client.py` for the HTTP client, `schema.py` for the pyarrow schema + normalization, `report.py` for run reporting). Cross-Lambda helpers live in `src/shared/` (`paths`, `s3_io`, `ssm`, `sns`, `snowflake_io`, `time_window`) and are copied into the build dir at the zip root, so handlers import them as `from shared import …`. `snowflake_io.copy_into(...)` is source-agnostic — the caller builds the SQL, so the daily handler and the backfill share it.
- Build artifacts are named `<prefix>-<source>-ingest.zip` (e.g. `zeus-dev-eia-ingest.zip`) under `infra/build/`. Each zip contains: pip-installed deps + the handler dir's `*.py` + `src/shared/`.
- The EIA pipeline is its own Terraform root under `infra/pipelines/eia/`. The root is self-contained: `remote_state.tf` consumes `infra/core` outputs (bucket, alerts topic), `locals.tf` holds the BA list, and `main.tf` authors the SSM `SecureString`, calls `module "lambda_job"` once, and wires the EventBridge daily rule (targeting the Lambda directly), the `aws_lambda_permission`, and the async `aws_lambda_function_event_invoke_config` (on-failure → SNS). The only shared module is `infra/modules/lambda_job/`, which owns Lambda packaging and IAM.
- Naming convention: `${project}-${env}-<source>-<resource>` (e.g. `zeus-dev-eia-ingest`). Resource names are derived in the pipeline root from `local.prefix` + the source name.
- SSM paths follow `/${project}/${env}/<source>/<name>` (e.g. `/zeus/dev/eia/api_key`, `/zeus/dev/snowflake/eia_loader_private_key`), constructed in the pipeline root.
- Secrets never in code, never in `terraform.tfvars`, never in `terraform.tfstate`. SSM `SecureString` with `lifecycle.ignore_changes = [value]`; rotate via `aws ssm put-parameter`. Snowflake auth follows this too: only the loader's **public** key is in Terraform (`var.eia_loader_public_key`); the private key lives in SSM.
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
- For Snowflake-touching changes: confirm rows land in `ZEUS_DEV.EIA.EIA_GRID` with real `period` timestamps (not "Invalid date").
- No hardcoded values unless explicitly agreed.
