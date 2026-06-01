# Backfill

One-time, fully local scripts that backfill historical data into the same S3
layout the daily pipelines produce. No infrastructure — just `boto3` writing to
the existing bucket. Each source has its own folder; `eia/` is the first.

## EIA (`backfill/eia/`)

Backfills the EIA hourly fuel-type dataset. Two phases, run separately:

- **extract** — one API call per `(BA, year)`, splits rows by day in memory, writes
  one raw JSON per `(BA, day)`: `raw/eia/ingestion_year=…/ingestion_month=…/ingestion_day=…/<BA>.json`.
- **transform** — per day, consolidates that day's raw files (all BAs) into one
  Snappy Parquet: `curated/eia/ingestion_year=…/…/eia_grid.parquet`.

Both reuse the production code unchanged (`src/shared/`, the EIA Lambda
`client.py` and `schema.py`) via `_bootstrap.py`.

### Run

```bash
export EIA_API_KEY=...   # or pull from SSM:
# export EIA_API_KEY=$(aws ssm get-parameter --name /zeus/dev/eia/api_key --with-decryption --query Parameter.Value --output text)

cd backfill/eia

# 1) extract raw history, then 2) build curated parquets
uv run python run.py extract   --start 2017-01-01 --concurrency 10
uv run python run.py transform --start 2017-01-01 --concurrency 10
```

`--end` defaults to today (UTC). Override `BUCKET` / `SOURCE` via env if needed.

### Concurrency

Default 10 threads. The fetch wrapper retries on HTTP 429 (honoring `Retry-After`),
so 20 is safe. To "ramp up": run at 10 first, check the log for 429s, then re-run at
`--concurrency 20` — idempotency (below) makes the re-run skip everything already
written and only finish the remainder.

### Idempotency & resuming

Before writing, each phase lists the year's S3 prefix once and skips keys already
present (never a per-file HEAD). A killed run is resumed by just re-running the same
command. Genuinely empty `(BA, year)` ranges (e.g. pre-mid-2018, where the endpoint
has little/no data) write nothing and are cheaply re-probed on resume.

### Logs & progress

The **terminal** shows simple progress only — `processing <BA> <year>...` /
`<BA> <year> done (...)` for extract, `<day> done (...)` for transform, a per-year
header, and a final `DONE` summary.

The **full detail** (every `OK / EMPTY / SKIP / FAILED` line and the per-year
SUMMARY, by BA and period) goes to a timestamped file under `backfill/eia/logs/`
(gitignored). Use it to diagnose and retry failures — the terminal just tracks
overall progress.
