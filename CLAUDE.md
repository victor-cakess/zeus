# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Operational commands and per-pipeline detail (secrets paths, observed performance, fault tolerance, build troubleshooting) live in [RUNBOOK.md](RUNBOOK.md).** This file holds the orientation, contracts, and conventions you need to *change* the platform.

## Project

Python 3.12+ project managed with `uv`. The repo is a data platform for energy data ingestion and modeling. One Step Functions state machine (`zeus-dev-daily-pipeline`) runs the whole daily flow: three ingestion pipelines in parallel land data in S3 and load it into Snowflake landing tables — **EIA** (hourly fuel-type → `ZEUS_DEV.EIA.EIA_GRID`), **NOAA** (daily weather summaries → `ZEUS_DEV.NOAA.NOAA_GRID`), and **FRED** (national energy price series → `ZEUS_DEV.FRED.FRED_GRID`) — then a container-image Lambda (`zeus-dev-dbt-run`) runs `dbt build` (models + tests) over `transform/`, then `zeus-dev-reports-digest` emails one combined run-report covering all sources plus the dbt run. The digest step **always runs**, even when an upstream step failed. EIA and NOAA are deliberately keyed on the same balancing-authority codes (`ba`) so weather joins to grid data downstream; FRED is national (no `ba`) and joins on date.

## Repository layout

```
.github/workflows/ci.yml          # PR/push offline gates: gitleaks + dbt parse + terraform fmt/validate (see Working norms → CI)
.github/workflows/dbt-clone-ci.yml # PR gate (transform/** only): dbt build against a zero-copy clone of ZEUS_DEV, then drop it
.github/workflows/dbt-deploy.yml  # CD (push to dev): build/push dbt image via OIDC + update-function-code + smoke-invoke (Phase 2.5)
infra/
  cicd/                           # Terraform root: GitHub OIDC provider + least-priv deploy role for dbt-deploy.yml (apply once)
  core/                           # Terraform root: shared S3 bucket + SNS alerts topic + Snowflake warehouse/db + per-source Snowflake DDL
    backend.tf, providers.tf, variables.tf, locals.tf, main.tf, sns.tf, outputs.tf
    snowflake.tf                  # shared ZEUS_DEV database
    snowflake_eia.tf              # module "eia_landing"  — EIA storage integration + ZEUS_DEV.EIA DDL + loader
    snowflake_noaa.tf             # module "noaa_landing" — NOAA storage integration + ZEUS_DEV.NOAA DDL + loader
    snowflake_fred.tf             # module "fred_landing" — FRED storage integration + ZEUS_DEV.FRED DDL + loader
    snowflake_transform.tf        # ZEUS_DEV_TRANSFORMER role + key-pair user (dbt: read landing, own modeled schemas) — per-source read grants live here too
    snowflake_ci.tf               # ZEUS_DEV_CI role + key-pair user (GitHub Actions dbt clone CI; inherits transformer)
  modules/
    lambda_job/                   # one Lambda + IAM role + ZIP-via-S3 packaging
    snowflake_landing/            # one source's Snowflake landing stack (integration + IAM trust + schema/table/stage + key-pair loader); names derived from source_name
  pipelines/
    eia/                          # EIA pipeline root: SSM (api key + snowflake key) + lambda_job; exports function_arn + balancing_authorities
    noaa/                         # NOAA pipeline root: SSM (snowflake key only — NCEI needs no api key) + lambda_job; same outputs
    fred/                         # FRED pipeline root: SSM (api key + snowflake key) + lambda_job; exports function_arn + series
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
    fred/ingest/                  # FRED Lambda: handler.py + client.py (series/observations, SERIES map, drops "." placeholders) + schema.py (narrow, series/date/value) + requirements.txt
    digest/                       # digest Lambda: handler.py reads each source's run_report.json + the dbt report, sends one combined email (requirements.txt: boto3 only)
    dbt/                          # dbt runner Lambda (container image): Dockerfile (bakes transform/ + dbt deps) + handler.py (thin: key → patches → dbt build → report) + lambda_mp_patch.py (/dev/shm patches) + requirements.txt
transform/                        # dbt project (staging + intermediate + marts models + tests; profiles.yml env-var driven, key-pair auth) — COPYed into the dbt image at build
  DECISIONS.md                    # modeling/business-rule decision records (M-N entries; infra ADRs live in repo-root ADR.md)
extraction/                       # gitignored, exploratory notebooks
backfill/                         # one-off historical backfill scripts (reuse src/ via _bootstrap.py); see backfill/README.md
  eia/                            # fetch→raw→curated→COPY: run.py (extract/transform) + fetch.py + extract.py + transform.py + snowflake_load.py + units.py (BA list) + _bootstrap.py + logconf.py
  noaa/                           # same two-phase pattern (run.py/fetch/extract/transform/snowflake_load + _bootstrap + logconf; stations from src client, no units.py); day-partitioned, backdated, idempotent resume
  fred/                           # same two-phase pattern (series from src client's SERIES map); one request per series for the whole range, FRED_API_KEY env for extract; backfilled 2014→2026
README.md                         # project overview + diagrams + how to run (front door)
ADR.md                            # architectural decision records (infra/platform; cross-cutting + per-pipeline)
RUNBOOK.md                        # operational commands + per-pipeline detail (secrets paths, perf, troubleshooting)
```

## Pipelines (contracts)

Operational detail (secrets paths, observed perf, fault-tolerance, runtime gotchas) is in [RUNBOOK.md](RUNBOOK.md) → "Per-pipeline operational reference". This section is the contract only.

### Daily orchestration (production)

- **One state machine runs everything:** `zeus-dev-daily-pipeline` (root `infra/pipelines/orchestration/`, **apply LAST**). EventBridge rule fires `cron(0 7 * * ? *)` (07:00 UTC = 04:00 sa-east-1) → `states:StartExecution` with input `{}` (the BA payloads are baked into the definition).
- **Shape:** `Ingest` (Parallel: EIA + NOAA + FRED branches) → `Dbt` → `Digest` → `CheckFailures` (Choice) → `Success` / `NotifyFailure` → `Fail`. Each ingest branch **catches its own failure** and normalizes to `{source, failed}` Pass states, so the Parallel always completes; the Dbt step's Catch routes straight to the digest — **the digest always runs**. `CheckFailures` inspects `$.ingest[i].failed` / `$.dbtError` / `$.digestError`; on any failure it publishes the full execution state to `zeus-dev-alerts` and marks the execution **Failed**.
- **Single source of truth:** one `ingest_sources` local (fed by the eia/noaa roots' outputs via `terraform_remote_state`) derives the ingest Branches, the `CheckFailures` `$.ingest[i]` rules, and the SFN role's invoke list — adding a source is one entry + its remote-state block, and branch order can't diverge from the failure checks. The dbt/digest ARNs come from their roots' outputs the same way; the orchestration root duplicates nothing.
- **Deliberately source-level:** SFN orchestrates at the source level only; the per-BA fan-out stays inside each ingest Lambda's thread pool. Per-BA detail lives in the run reports + digest.

### Ingestion — EIA / NOAA / FRED (production)

Three Lambdas, **same orchestration shape via the shared `ingest.run_ingest`**: the state machine invokes each **synchronously** with `{"units": [...]}` (the list exported by its pipeline root, baked into the ASL) → fan out per-unit fetch (one raw JSON each) → consolidate the day's partition to one Snappy Parquet → `COPY INTO <SOURCE>_GRID` → write `run_report.json`. They do **not** email — the digest does. Per-unit fault tolerance: a unit that errors or returns 0 rows is `skipped` and the run continues; only a **total outage** (0 rows) fails the invocation. All Python 3.12, 1024 MB, 300 s, deployed via S3.

| Source | Function | Table | Grain | Units | Lookback | Notes |
|---|---|---|---|---|---|---|
| EIA | `zeus-dev-eia-ingest` | `ZEUS_DEV.EIA.EIA_GRID` | `(period, respondent, fueltype)` | 71 BAs (`infra/pipelines/eia/locals.tf`) | 7-day | hourly fuel-type; API key |
| NOAA | `zeus-dev-noaa-ingest` | `ZEUS_DEV.NOAA.NOAA_GRID` | `(ba, station, date)`, wide (13 datatypes) | 14 BAs → stations via `STATIONS` map in client.py | 7-day | no API key (NCEI) |
| FRED | `zeus-dev-fred-ingest` | `ZEUS_DEV.FRED.FRED_GRID` | `(series, date)`, narrow | 15 series via `SERIES` map in client.py | 150-day | national, no `ba`; API key; 150d re-captures PPI revisions |

S3 layout is identical across all three (`raw/<source>/.../<unit>.json`, `curated/<source>/.../<source>_grid.parquet`, `reports/<source>/.../run_report.json`).

### dbt build (production)

- **Worker:** container-image Lambda `zeus-dev-dbt-run` (root `infra/pipelines/dbt/`; image in ECR, tag = commit SHA, **built + deployed by CI on merge to dev — Phase 2.5**; the Terraform root provisions the Lambda/ECR but **ignores `image_uri`**). The image bakes `transform/` + `dbt deps`; the handler runs `dbt build` as `ZEUS_DEV_TRANSFORMER` (key-pair, private key SSM `/zeus/dev/snowflake/transformer_private_key`).
- **Report-before-raise:** writes `reports/dbt/.../run_report.json` (`models_built`, `tests_passed`/`tests_failed`, failed-test names) **before** raising on failure — so the digest email always carries the dbt detail. A `dbt build` failure (including failing tests) fails the step; the Catch routes to the digest, then `CheckFailures` alerts + fails the execution.
- **Lambda has no `/dev/shm`** → multiprocessing SemLock patches in `src/lambdas/dbt/lambda_mp_patch.py` (`apply()` runs before the dbt import; rationale in its docstring). HOME/target/log redirected to `/tmp`. (Full mechanism in RUNBOOK.)

### Daily digest (production)

- **Trigger:** the final state-machine step — runs **always**, even when an ingest or dbt step failed (their Catches route to it). Invoked synchronously with `{}` (`zeus-dev-reports-digest`, 256 MB, 60 s).
- **Contract:** for each source in `SOURCES` (`eia,noaa,fred`) reads today's run report + the dbt report and publishes **one** combined email to `zeus-dev-alerts`. dbt is **not** a fan-out source, so `SOURCES` stays `eia,noaa,fred`. **Adding a future source = append it to `var.sources`.** Reuses `src/shared/report.py` (`format_digest`, `format_dbt_section`). **SNS caps subjects at 100 ASCII chars** — the subject is deliberately static; all detail lives in the body.

### Snowflake (live)

- **Shared:** warehouse `ZEUS_DEV_WH` (x-small, auto-suspend 60 s, auto-resume) in `infra/core/main.tf`; database `ZEUS_DEV` in `infra/core/snowflake.tf`.
- **Per-source landing stacks** are one `module "snowflake_landing"` call each (`snowflake_eia.tf`, `snowflake_noaa.tf`, `snowflake_fred.tf`). The module derives every name from `source_name` and takes the table columns as a variable. Each call creates: a storage integration (`ZEUS_DEV_<SOURCE>_S3_INT`) + paired AWS IAM role (`zeus-dev-snowflake-<source>`), the schema + `<SOURCE>_GRID` table + `<SOURCE>_STAGE` over `curated/<source>/`, and a least-privilege key-pair loader (`ZEUS_DEV_<SOURCE>_LOADER`, USAGE + INSERT only). **The module grants nothing to the transformer** — onboarding a source also means adding its USAGE+SELECT grant pair in `snowflake_transform.tf`, or dbt can't read the new schema.
  - **EIA:** `ZEUS_DEV.EIA.EIA_GRID`, stage `EIA_STAGE`. **NOAA:** `ZEUS_DEV.NOAA.NOAA_GRID`, stage `NOAA_STAGE`. **FRED:** `ZEUS_DEV.FRED.FRED_GRID`, stage `FRED_STAGE`.
- **Load path:** the ingest Lambda's `COPY INTO ... FILE_FORMAT = (TYPE = PARQUET USE_LOGICAL_TYPE = TRUE) MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE`. **`USE_LOGICAL_TYPE = TRUE` is required** — without it EIA's `period` loads as a raw INT64 and NOAA's `date` as a raw INT32 ("Invalid date").
- **Auth:** public key in Terraform (`var.eia_loader_public_key` / `var.noaa_loader_public_key`), private key in SSM. Creating integrations + service users requires the `infra/core/` Snowflake provider to run as `ACCOUNTADMIN`.
- **Transformer (dbt):** account role `ZEUS_DEV_TRANSFORMER_ROLE` + key-pair service user `ZEUS_DEV_TRANSFORMER` (`infra/core/snowflake_transform.tf`) — read-only on the landing schemas, `CREATE SCHEMA` on `ZEUS_DEV`, owns the modeled schemas; rolled up to SYSADMIN. Public key in tfvars (`var.transformer_public_key`), private key in SSM (`/zeus/dev/snowflake/transformer_private_key`); manual local dbt runs keep using the gitignored `sf_transformer.p8`.
- **CI clone runner:** account role `ZEUS_DEV_CI_ROLE` + key-pair service user `ZEUS_DEV_CI` (`infra/core/snowflake_ci.tf`) — `CREATE DATABASE` on the account, USAGE on `ZEUS_DEV` + the warehouse, **plus the transformer role granted into it** (cloned child objects keep source grants/ownership, so only transformer privileges work inside a clone). Public key in tfvars (`var.ci_public_key`); private key in GitHub Actions secrets (`SNOWFLAKE_CI_PRIVATE_KEY`), local copy `sf_ci.p8` (gitignored). Used only by `.github/workflows/dbt-clone-ci.yml`.
- **Table contract:** append-only landing; duplication from the lookback overlap is deduped downstream in dbt (EIA on `(period, respondent, fueltype)`, NOAA on `(date, station)`, FRED on `(series, date)`) keeping the latest `ingestion_date`. Per-file load metadata makes same-day re-runs idempotent.
- **Module note:** EIA's landing resources were originally inline in `snowflake_eia.tf`; they were moved into the module via `terraform state mv` (no destroy/recreate — the module reproduces every name/comment exactly). See README cross-cutting decision for the move list.

## Pre-commit hooks

- **gitleaks** — scans for secrets before every commit. Commits will be blocked if secrets are detected.

## Decisions reference

`ADR.md` (repo root) records the architectural decisions for the platform — cross-cutting decisions (region, Terraform structure, S3 layout, secrets, packaging, observability, shared helpers) at the top, pipeline-specific decisions in their own sections. Modeling and business-rule decisions for the dbt layer (grain, metric definitions, join semantics) live in `transform/DECISIONS.md` (`M-N` entries, same format); the enforceable contract (tests, column docs) stays in each layer's `schema.yml`. Read the relevant file before proposing structural or modeling changes.

## Working norms

### Code & infra
- Lambda source lives in `src/lambdas/<source>/ingest/`. `handler.py` is thin orchestration; the only genuinely source-specific modules are `client.py` (HTTP client) and `schema.py` (pyarrow schema + normalization). Everything else is shared in `src/shared/` (`paths`, `s3_io`, `ssm`, `sns`, `snowflake_io`, `time_window`, `report`, **`ingest`**) and copied into the build dir at the zip root, so handlers import them as `from shared import …`. `ingest.run_ingest(...)` is the shared daily orchestrator (source-specific pieces injected); `report.py` is source-agnostic (takes `source` as a param) and used by both ingest Lambdas and the digest. `snowflake_io` owns the key-pair connection (`copy_into`) and the `COPY INTO` statement builder (`copy_statement`); the daily handlers build SQL via the latter (the backfills keep their own inline statement).
- Per-source `handler.py` is a thin shim (~12 lines) over the shared `src/shared/ingest.py` orchestrator: it builds `ingest.config_from_env()` and calls `ingest.run_ingest(...)`, injecting the only two things that differ per source — the `fetch_fn` (EIA wraps its api-keyed client in a closure to match the `(unit, start, end)` contract) and the `window_fn` (`lookback_window` hourly vs `lookback_window_dates` daily) — plus the source's `schema`/`normalize_row`. The fan-out → consolidate → COPY → report orchestration lives once in `ingest.run_ingest`.
- Build artifacts are named `<prefix>-<source>-ingest.zip` (e.g. `zeus-dev-eia-ingest.zip`) / `<prefix>-reports-digest.zip` under `infra/build/`. Each zip contains: pip-installed deps + the handler dir's `*.py` + `src/shared/`. The dbt runner ships as a **container image** instead (ECR repo `zeus-dev-dbt-run`, tag = commit SHA, built + pushed by the `dbt-deploy` CD workflow on merge to dev — not by Terraform; docker build context = repo root, allowlisted by the repo-root `.dockerignore`) because `dbt-snowflake` doesn't fit the zip limits.
- Each pipeline (`eia`, `noaa`, `digest`, `dbt`) is its own self-contained Terraform root under `infra/pipelines/<name>/`: `remote_state.tf` consumes `infra/core` outputs (bucket, alerts topic, snowflake account/warehouse/db), `locals.tf` holds the unit list, and `main.tf` authors any SSM `SecureString` plus the Lambda — zip roots call `module "lambda_job"` once; the dbt root authors an ECR repo + fingerprinted docker build/push + an inline `package_type = "Image"` Lambda (`lambda_job` is zip-only by design). **Pipeline roots own no triggers or failure routing** — both live in `infra/pipelines/orchestration/`, whose state machine consumes each root's `*_function_arn` (and `balancing_authorities` for the ingests) outputs via remote state. Apply order: core → pipeline roots → orchestration last. Two shared modules: `infra/modules/lambda_job/` (zip Lambda packaging + IAM) and `infra/modules/snowflake_landing/` (one source's Snowflake landing stack, consumed by `infra/core`, names derived from `source_name`).
- Historical backfills (`backfill/<source>/`) mirror the daily pipeline's S3 layout: a two-phase `run.py extract|transform` writes one raw JSON per `(unit, observation-day)` and one curated Parquet per observation-day (partition **backdated** to the observation date, `ingestion_date` = that day), then `snowflake_load.py` whole-stage COPYs. Both phases skip already-written keys for idempotent resume, or take `--overwrite` to re-write them (used to add BAs to an already-backfilled range — see the backfill note in RUNBOOK). `backfill/noaa/run.py` forces IPv4 because the dev machine's IPv6 route to NCEI is broken (urllib3 prefers IPv6 → ~10 min/request stall otherwise). Backfill scripts may repeat patterns rather than share code with the Lambda — they reuse `src/` modules via `_bootstrap.py` (sets `sys.path`).
- Naming convention: `${project}-${env}-<source>-<resource>` (e.g. `zeus-dev-eia-ingest`). Resource names are derived in the pipeline root from `local.prefix` + the source name.
- SSM paths follow `/${project}/${env}/<source>/<name>` (e.g. `/zeus/dev/eia/api_key`, `/zeus/dev/snowflake/eia_loader_private_key`), constructed in the pipeline root.
- Secrets never in code, never in `terraform.tfvars`, never in `terraform.tfstate`. SSM `SecureString` with `lifecycle.ignore_changes = [value]`; rotate via `aws ssm put-parameter`. Snowflake auth follows this too: only each loader's **public** key is in Terraform (`var.eia_loader_public_key` / `var.noaa_loader_public_key`); the private keys live in SSM. The private-key files (`*.p8`) and `sf_*_loader.pub` are gitignored.
- Lambda packaging is ZIP built locally via `uv pip install --python python3.12 --target …`, then **uploaded to S3** and referenced by `s3_bucket`/`s3_key` (the package is ~49 MiB zipped, over the 50 MiB direct-upload limit). Don't rely on the project venv for the build; `unset VIRTUAL_ENV` first. `uv` and `python3.12` must be on PATH when running `terraform apply`. The `null_resource.build` trigger fingerprints `requirements.txt` + `**/*.py` under both the Lambda's `src_dir` and `src/shared/`, so any change forces a rebuild. `snowflake-connector-python` pins `cryptography`/`pyOpenSSL` (loose upstream bounds otherwise resolve to an import-incompatible pair).
- Event payload contract: the state machine invokes each ingest Lambda with `{"units": [...]}` (baked into the ASL from the pipeline roots' `balancing_authorities` outputs); the handler reads `event["units"]`. The same contract works for any fan-out source. The dbt and digest steps take `{}`.
- Shared AWS clients (`src/shared/{s3_io,ssm,sns,snowflake_io}.py`) are module-level singletons by convention; tests substitute them by monkeypatching module attributes (see the dbt container test harness). Keep new shared helpers consistent.

### dbt layering (every source walks the same four steps)

- **landing** (`<SOURCE>.<SOURCE>_GRID`) — truth as received: append-only, raw API names, duplicated by the lookback overlap. Never edited, never queried by consumers.
- **staging** (`stg_<source>__*`, view) — cleaning only: dedup the lookback overlap (keep latest `ingestion_date`) + rename to standard names. Same grain as landing, zero judgment — if a transformation requires a decision, it does NOT belong here.
- **intermediate** (`int_<source>__*`, view) — business rules and grain changes (aggregation, derived metrics, unit rollups). Every judgment call gets an `M-N` entry in `transform/DECISIONS.md`; the enforceable contract goes in the layer's `schema.yml`.
- **marts** (`fct_*`, table/incremental) — the cross-source consumption surface and the only layer consumers may depend on. No new business logic beyond cross-source joins/rollups; materialization per M-5.

Onboarding a new source (e.g. FRED) = one `stg_` + one or more `int_` models + sources yml + tests, then join it into (or alongside) the marts. Schemas come from `generate_schema_name` (STAGING / INTERMEDIATE / MARTS).

### CI (GitHub Actions)
- **Phase 1 (live):** `.github/workflows/ci.yml` runs the offline gates on every PR and push to main — pre-commit (gitleaks), `dbt parse` (dummy creds; parse doesn't connect), and per-root `terraform fmt -check` + `validate` (`init -backend=false` — no state, no cloud creds). It automates the cheap end of "Done means"; the AWS-touching tiers stay local.
- **Phase 2 (live — dbt clone CI):** `.github/workflows/dbt-clone-ci.yml` runs on PRs touching `transform/**`: `CREATE OR REPLACE DATABASE ZEUS_CI_PR_<n> CLONE ZEUS_DEV` (zero-copy) → `dbt build` against the clone (`SNOWFLAKE_DATABASE` env var; the sources yml resolves `target.database`, so landing reads come from the clone too — prod untouched) → `DROP` with `if: always()`. Clone lifecycle = `dbt run-operation` macros in `transform/macros/ci/clone.sql` (prefix hardcoded + digits-only suffix, so they can't touch non-CI databases; `CREATE OR REPLACE` self-heals leaked clones). Auth: service user `ZEUS_DEV_CI` / role `ZEUS_DEV_CI_ROLE` (`infra/core/snowflake_ci.tf`) — the transformer role is granted into the CI role because **cloned child objects keep the source's grants/ownership** (the clone owner owns only the database shell), so only transformer privileges work inside the clone. GitHub secrets: `SNOWFLAKE_ACCOUNT` + `SNOWFLAKE_CI_PRIVATE_KEY`. Forked PRs can't read secrets and fail at the key step (fine for a solo repo). Cost ~a few cents/run. Upgrade path: Slim CI (`state:modified+`) once a production manifest is stored.
- **Incremental gotcha:** the clone carries the marts' state, so `dbt build` exercises the real incremental-merge path — but a PR that changes an incremental model's schema will fail in CI until it handles `on_schema_change` (or the PR is built `--full-refresh` after merge), which is the correct signal, not a CI bug.
- **Phase 2.5 (live — dbt image CD):** `.github/workflows/dbt-deploy.yml` runs on **push to `dev`** touching `transform/**`, `src/lambdas/dbt/**`, or `src/shared/**` (+ `workflow_dispatch`): assume the `zeus-dev-dbt-deploy` role via **GitHub OIDC** (no long-lived keys) → `docker build` (same Dockerfile + repo-root context as the old `local-exec`) → push to ECR tagged by commit SHA → `aws lambda update-function-code` → **smoke-invoke** `zeus-dev-dbt-run` (`dbt build`) as the deploy gate (fails red on `status != ok` or any failed test). This **decouples the image deploy from Terraform**: the workflow owns the image, the dbt root owns the Lambda infra with `lifecycle.ignore_changes = [image_uri]` so `apply` never reverts a CD deploy — so it needs no remote state and works while state is still local (unlike Phase 3). The OIDC provider + role live in `infra/cicd/` (apply once); the role ARN goes in the GitHub Actions **variable** `AWS_DEPLOY_ROLE_ARN`. The PR's Phase-2 clone CI validates the models; this ships the validated result on merge. Bootstrap: a from-scratch env must push one image (via `workflow_dispatch` or a manual build) before the dbt Lambda can be created. Generalizing this to the zip Lambdas (eia/noaa/fred/digest) is the obvious next step.
- **Phase 3 (deferred — Terraform plan/apply on PRs):** blocked on migrating Terraform state from local to an S3 backend (+ DynamoDB locking); AWS OIDC is now solved (Phase 2.5 — reuse the provider). The `local-exec` zip builds also need uv/python3.12 on the runner. Do the backend migration as its own project first; don't bolt it onto CI.
- **Build-time gotchas** (`/bin/sh: uv: not found`, Docker/ECR auth at apply, `Provider produced inconsistent final plan`) are documented in [RUNBOOK.md](RUNBOOK.md) → "Build troubleshooting".

### S3 layout
- Two layers: `raw/<source>/` (one JSON file per atomic unit, e.g. per BA) and `curated/<source>/` (one consolidated Parquet per day). A `reports/<source>/` prefix holds per-run JSON reports.
- Multi-level Hive partitioning on date for all layers (`ingestion_year=/ingestion_month=/ingestion_day=/`).
- Raw filename = `<unit>.json`. No `<unit>=` partition in the path — the unit is in the data and the filename.
- Curated filename = `<source>_grid.parquet` (e.g. `eia_grid.parquet`). Snappy compression.
- Append-only, immutable. Downstream de-dup at query time, not write time.

### Done means

Verification is proportional to the **blast radius of the change** — match the test to what the change can actually break, not to the size of the pipeline.

- Terraform `validate` passes and `plan` is clean.
- **dbt models/tests only (`transform/`):** local `dbt build` green (models + tests) + a spot-check query of the changed columns, then **merge the PR to `dev`** — the `dbt-deploy` CD workflow (Phase 2.5) rebuilds the image, runs `update-function-code`, and smoke-invokes `zeus-dev-dbt-run` automatically; watch it go green. **No state-machine run, no re-ingestion** — a model change can't break ingestion or wiring, and a full DAG run can fail for reasons unrelated to the change. The deploy is NOT optional but it is **no longer manual**: the dbt project is **baked into the image**, so a local-only build is silently reverted by the next cron — merging to `dev` is what ships it. (For a hotfix you can still `workflow_dispatch` the deploy or, in a pinch, build/push + `update-function-code` by hand.)
- **dbt runner changes (handler / Dockerfile / deps / mp patches):** local container gate (run the handler in the image with `_multiprocessing.SemLock` stubbed to raise), then merge to `dev` and confirm the `dbt-deploy` smoke-invoke passes (same CD path as model changes).
- **Ingestion Lambda changes:** smoke-test one invocation end-to-end (Lambda → S3 → Snowflake) before declaring done; for pipeline changes, invoke once with the full units list and confirm all expected raw + curated partitions land.
- **Orchestration changes:** one `aws stepfunctions start-execution` end-to-end — the wiring itself is under test: all states green, the digest email arrives (with the dbt section), and a deliberately failed step still runs the digest + fires the SNS alert + marks the execution Failed.
- For Snowflake-touching changes: confirm rows land in the source's landing table (`ZEUS_DEV.EIA.EIA_GRID` / `ZEUS_DEV.NOAA.NOAA_GRID`) with real timestamps/dates (`period` / `date`), not "Invalid date".
- No hardcoded values unless explicitly agreed.

## Tech debt

Known issues to fix later. Not blocking; documented here so they aren't lost.

- **Lambda IAM scaffolding duplicated.** `infra/modules/lambda_job/iam.tf` and the dbt root (`infra/pipelines/dbt/main.tf`) each define the role + basic-execution attachment + inline-policy trio. Two copies is acceptable; **the third copy (next container-image Lambda) is the trigger** to extract a shared `lambda_iam` module (use `moved` blocks — no role recreation).
