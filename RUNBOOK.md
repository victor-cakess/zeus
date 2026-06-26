# RUNBOOK.md

Operational reference for the Zeus data platform — the commands you run and the
per-pipeline detail you grep while operating. Conventions and contracts that govern
how to *change* the platform live in [CLAUDE.md](CLAUDE.md); architectural decisions
live in [ADR.md](ADR.md) and [transform/DECISIONS.md](transform/DECISIONS.md); the
project overview + diagrams are in [README.md](README.md).

## Commands

```bash
# Python deps
uv sync --group dev
uv run pre-commit install
uv run pre-commit run --all-files

# Terraform — core (S3 + SNS + Snowflake warehouse/db + per-source landing stacks); apply this first.
# Requires in terraform.tfvars (gitignored): role = "ACCOUNTADMIN" (to create the
# storage integrations + service users) and one loader RSA public-key body per source
# (eia_loader_public_key, noaa_loader_public_key, fred_loader_public_key). Generate each key-pair once, e.g.:
#   openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out sf_noaa_loader.p8 -nocrypt
#   openssl rsa -in sf_noaa_loader.p8 -pubout -out sf_noaa_loader.pub   # paste body into tfvars
# (*.p8 and *.pub are gitignored.) Same pattern for the transformer (sf_transformer.p8 →
# var.transformer_public_key) and the clone-CI user (sf_ci.p8 → var.ci_public_key).
cd infra/core
terraform init
terraform plan
terraform apply

# GitHub Actions secrets for the dbt clone CI (.github/workflows/dbt-clone-ci.yml)
gh secret set SNOWFLAKE_ACCOUNT --body "$(terraform -chdir=infra/core output -raw snowflake_account)"
gh secret set SNOWFLAKE_CI_PRIVATE_KEY < sf_ci.p8
terraform output                          # bucket_name, bucket_arn, alerts_topic_arn, snowflake_warehouse_name, snowflake_account, snowflake_database_name

# Terraform — EIA pipeline
# The build step runs `uv pip install --python python3.12` via Terraform's /bin/sh
# provisioner, so uv AND python3.12 must be REAL binaries on PATH. If apply fails
# with "/bin/sh: uv: not found", see "Build troubleshooting" below.
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

# Terraform — FRED pipeline (same shape as EIA: api key + loader key)
cd infra/pipelines/fred
terraform init && terraform apply
aws ssm put-parameter --name /zeus/dev/snowflake/fred_loader_private_key \
  --value "$(cat sf_fred_loader.p8)" --type SecureString --overwrite
aws ssm put-parameter --name /zeus/dev/fred/api_key \
  --value "$FRED_API_KEY" --type SecureString --overwrite
aws lambda invoke --function-name zeus-dev-fred-ingest \
  --payload '{"units":["WTI","COALPPI"]}' --cli-binary-format raw-in-base64-out \
  /tmp/fred-out.json && cat /tmp/fred-out.json
#   then: SELECT min(date), max(date), count(*) FROM ZEUS_DEV.FRED.FRED_GRID;

# Terraform — digest pipeline (one combined daily email across all sources)
cd infra/pipelines/digest
terraform init && terraform apply
aws lambda invoke --function-name zeus-dev-reports-digest \
  --payload '{}' --cli-binary-format raw-in-base64-out /tmp/digest-out.json && cat /tmp/digest-out.json

# Terraform — dbt runner (container image). Provisions the ECR repo + Lambda only;
# the IMAGE is built/deployed by CI (.github/workflows/dbt-deploy.yml) on merge to dev,
# NOT by terraform apply (image_uri is ignored — see CI Phase 2.5 in CLAUDE.md).
cd infra/pipelines/dbt
terraform init && terraform apply
aws ssm put-parameter --name /zeus/dev/snowflake/transformer_private_key \
  --value "$(cat sf_transformer.p8)" --type SecureString --overwrite
# Deploy the image: merge a transform/** or src/lambdas/dbt/** change to dev (the CD
# workflow builds + update-function-code + smoke-invokes), or trigger it manually:
gh workflow run dbt-deploy.yml --ref dev
# Manual fallback (no CI) — build/push by hand then repoint the Lambda:
#   aws ecr get-login-password --region sa-east-1 | docker login --username AWS --password-stdin <acct>.dkr.ecr.sa-east-1.amazonaws.com
#   docker build --platform linux/amd64 -t <acct>.dkr.ecr.sa-east-1.amazonaws.com/zeus-dev-dbt-run:$(git rev-parse HEAD) -f src/lambdas/dbt/Dockerfile .
#   docker push <acct>.dkr.ecr.sa-east-1.amazonaws.com/zeus-dev-dbt-run:$(git rev-parse HEAD)
#   aws lambda update-function-code --function-name zeus-dev-dbt-run --image-uri <same tag>

# One-time CD setup (infra/cicd): OIDC provider + deploy role, then publish the role ARN
cd infra/cicd && terraform init && terraform apply
gh variable set AWS_DEPLOY_ROLE_ARN --body "$(terraform -chdir=infra/cicd output -raw deploy_role_arn)"

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
FRED_API_KEY=$(aws ssm get-parameter --name /zeus/dev/fred/api_key --with-decryption \
  --query Parameter.Value --output text) \
  uv run python backfill/fred/run.py extract --start 2014-01-01       # one request per series (whole range)
uv run python backfill/fred/run.py transform --start 2014-01-01      # then snowflake_load.py (same env contract, sf_fred_loader.p8)
```

## Build troubleshooting

- **`/bin/sh: uv: not found` during `terraform apply`.** The Lambda build is a `local-exec` provisioner (`infra/modules/lambda_job/main.tf`) that Terraform runs under **`/bin/sh`** — a non-interactive, non-login shell that does **not** source your shell config. If `uv` is a shim/alias/shell function, or lives only on your interactive shell's PATH (common with version managers or a venv that doesn't ship `uv`), the build fails with `/bin/sh: uv: not found` **even though `uv` works fine in your terminal**. The fix is to put a **real `uv` binary** on a directory that's already on PATH for non-interactive shells — `~/.local/bin` works:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh   # installs uv + uvx to ~/.local/bin
  uv --version                                       # must resolve as a plain binary, not a shim
  ```
  Then re-run `terraform apply` (a half-failed apply just continues — it recreates the build and the remaining resources). `python3.12` must likewise be a real binary on PATH; the project's `.venv/bin/python3.12` satisfies that with the venv active. The build does `unset VIRTUAL_ENV` itself, so don't rely on the venv providing `uv`.
- **dbt root: Docker + ECR auth at apply time.** The dbt root's `null_resource.build` runs `aws ecr get-login-password | docker login` then `docker build --platform linux/amd64` + `docker push` under the same `/bin/sh` provisioner constraints — `docker` and `aws` must be real binaries on PATH. A zip-root apply may also fail once with `Provider produced inconsistent final plan` when the zip is rebuilt mid-apply (the archive hash changes between plan and apply); the immediate re-apply converges.

## Per-pipeline operational reference

The contracts (function name, table, grain, units) are in [CLAUDE.md](CLAUDE.md) → "Pipelines (contracts)". This section holds the operational detail — secrets paths, observed performance, fault tolerance, retries, runtime gotchas.

### Daily orchestration

- **Retries:** each `lambda:invoke` retries only the AWS-transient set (`Lambda.ServiceException`, `TooManyRequests`, `SdkClient`, `AWSLambda`; 2 attempts, backoff 2.0). Function errors (crashes, total outage) go straight to the branch Catch — retrying a code bug just doubles the run.
- **Visibility:** CloudWatch logging `level = ALL` + execution data (log group `/aws/vendedlogs/states/zeus-dev-daily-pipeline`), X-Ray tracing enabled.
- **Observed:** full execution (parallel ingest → dbt → digest) ~30 s end-to-end. The per-source fan-out stays inside each ingest Lambda's thread pool (EIA's 71-way: worst observed daily run 59 s vs the 300 s timeout); SFN orchestrates at the source level only, and per-BA detail lives in the run reports + digest.

### EIA daily ingestion

- **Per-invocation steps:** (1) fan out the per-BA fetch across a `ThreadPoolExecutor` (`MAX_WORKERS` env var, default 20), writing one raw JSON per BA; (2) read back the day's raw partition and consolidate to one Snappy Parquet in curated; (3) `COPY INTO ZEUS_DEV.EIA.EIA_GRID` from the external stage; (4) write `run_report.json`. It does **not** email — the digest does.
- **Secrets:** EIA API key in SSM `/zeus/dev/eia/api_key`; Snowflake loader private key in SSM `/zeus/dev/snowflake/eia_loader_private_key` (both `SecureString`). Lambda role has scoped `ssm:GetParameter` + `kms:Decrypt` on both. Each is fetched per run before it's needed.
- **Raw output:** `s3://zeus-dev-energy-data/raw/eia/ingestion_year=YYYY/ingestion_month=MM/ingestion_day=DD/<unit>.json`. Append-only, immutable; rolling 7-day lookback per run. Overlapping windows are intentional and de-duplicated downstream at query time on `(period, respondent, fueltype)`.
- **Curated output:** `s3://zeus-dev-energy-data/curated/eia/.../eia_grid.parquet`. Single Snappy-compressed Parquet per day. **Reports:** `s3://zeus-dev-energy-data/reports/eia/.../run_report.json` — succeeded + skipped units (with reasons).
- **Per-BA fault tolerance:** a BA that errors or returns 0 rows is recorded as `skipped` and the run continues, consolidating whatever landed. Only a **total outage** (0 rows consolidated) raises `ValueError` and fails the invocation. Inside a fetch, the client retries HTTP 502/503/504 with backoff (10 s, 20 s, 30 s) before giving up and skipping the BA.
- **Alerting:** failure routing lives in the state machine. Any unhandled crash (OOM, timeout, init error) or the total-outage `ValueError` is caught by the EIA branch's Catch; the digest still runs (EIA shows as "no report"), then `CheckFailures` publishes to `zeus-dev-alerts` and fails the execution.
- **Observed performance:** production daily runs 18–59 s, avg ~40 s (cold start + EIA API latency variance; CloudWatch `Duration`, June 2026 — a warm smoke-test run is ~12.5 s). Peak memory ~247 MB.

### NOAA daily ingestion

- **Trigger detail:** runs **in parallel with EIA and FRED** — nothing the parallel sources touch contends (different APIs, S3 prefixes, Snowflake tables/users). 14 BAs: `CISO, PJM, ERCO, MISO, ISNE, NYIS, SWPP, TVA, SOCO, DUK, FPL, BPAT, PSCO, SRP`.
- **Fan-out unit = BA.** Each BA maps to 3–10 weather stations (the `STATIONS` map in `src/lambdas/noaa/ingest/client.py`); the client fetches all of a BA's stations in **one batched NCEI request** (comma-separated `stations=`) and tags every row with its `ba`. Raw layout is one `<ba>.json` per BA, mirroring EIA.
- **Schema:** wide — 13 datatypes (`TMAX, TMIN, TAVG, PRCP, SNOW, SNWD, AWND, WSF2, WSF5, WDF2, RHAV, ASLP, ADPT`), metric units, plus `ingestion_date`. Absent datatypes land null. `lookback_window_dates` gives a rolling 7-day date window (NOAA data lags a few days, so the lookback catches late/QC-revised days).
- **Secrets:** Snowflake loader key only, SSM `/zeus/dev/snowflake/noaa_loader_private_key`. No api-key parameter (NCEI `daily-summaries` needs none).
- **Outputs:** `raw/noaa/.../<ba>.json`, `curated/noaa/.../noaa_grid.parquet`, `reports/noaa/.../run_report.json` — same partition scheme as EIA.
- **Observed performance:** production daily runs 5–50 s, avg ~13.5 s (CloudWatch `Duration`, June 2026).

### FRED daily ingestion

- **Units = 15 national price series** (slug → FRED series id in the `SERIES` map in `src/lambdas/fred/ingest/client.py`; `infra/pipelines/fred/locals.tf` mirrors the key set): 8 daily spots (`WTI, BRENT, HENRYHUB, HEATINGOIL, PROPANEMT, JETFUEL, GASNYH, GASGULF`), 2 weekly retail (`GASOLINE, DIESEL`), 5 monthly indexes (`COALPPI, NATGASPPI, ELECPPI, ELECPRICE, CPIENERGY`). No `ba` — FRED joins grid/weather data downstream on date.
- **Schema:** narrow — `series, series_id, date, value, ingestion_date`. The client drops FRED's `"."` placeholder observations, so an empty window is a skip, not a failure.
- **Lookback = 150 days** (`lookback_days` in `infra/pipelines/fred/variables.tf`, vs 7 for EIA/NOAA): keeps every frequency represented in every run and re-captures BLS PPI revisions (landing up to ~4 months after first release). ~900 rows/run appended; deduped downstream on `(series, date)` keep latest `ingestion_date`.
- **Secrets:** FRED API key in SSM `/zeus/dev/fred/api_key`; Snowflake loader key in `/zeus/dev/snowflake/fred_loader_private_key`.
- **Outputs:** `raw/fred/.../<SLUG>.json`, `curated/fred/.../fred_grid.parquet`, `reports/fred/.../run_report.json` — same partition scheme as EIA/NOAA.

### dbt build

- **Worker detail:** image in ECR repo `zeus-dev-dbt-run`, tag = commit SHA, built + deployed by CI on merge to dev (Phase 2.5); the Terraform root provisions the Lambda/ECR but ignores `image_uri`; 2048 MB, 300 s. The image bakes `transform/` + `dbt deps` at build time; the handler runs `dbt build` (models + tests) via `dbtRunner`, authenticating as `ZEUS_DEV_TRANSFORMER` (key-pair; private key SSM `/zeus/dev/snowflake/transformer_private_key`, fetched to `/tmp` per run).
- **Lambda runtime gotchas (isolated in `src/lambdas/dbt/lambda_mp_patch.py`, rationale in its docstring; the handler calls `apply()`):** Lambda has **no `/dev/shm`**, so multiprocessing SemLocks raise `FileNotFoundError` — `apply()` swaps dbt's mp context for `multiprocessing.dummy` **before** the `dbt.cli` import (Manifest binds the lock factory at class-definition time) and replaces ThreadPool's SemLock-backed change notifier with an `os.pipe()` shim. The image is read-only → `HOME` and dbt's target/log paths are redirected to `/tmp`.
- **Observed performance:** ~23 s per run (9 models, 52 tests), image cold-start init ~3.8 s, peak memory ~273 MB (of 2048).

### Daily digest

- **What it does (detail):** for each source in `SOURCES` (`eia,noaa,fred`), reads today's `reports/<source>/.../run_report.json` from S3, computes a 30-day skip history, and publishes **one** combined email to `zeus-dev-alerts` (succeeded/skipped counts + Snowflake rows loaded per source). It also reads `reports/dbt/.../run_report.json` and renders it as its own section (subject chip like `dbt 9 models / 52 tests` or `dbt FAILED 2 tests`, body lists failed-test names). A source or dbt run that wrote no report (it crashed → the execution-level alert already fired) is surfaced as "no report", not a crash. Reuses `src/shared/report.py` (`format_digest`, `format_dbt_section`).
- **SNS caps subjects at 100 ASCII chars** — the subject is deliberately static (`Zeus daily report — <date>`); all per-source/dbt detail lives in the body (per-source chips overflowed the cap at 3 sources).
