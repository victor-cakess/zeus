# Modeling decisions (dbt layer)

Business-rule and modeling decision records for the `transform/` dbt project — the
domain-logic counterpart to the infra ADRs in the repo-root `README.md`.

**Division of labor:** this file records *decisions* (the choice, the rejected
alternatives, the why); the enforceable *contract* lives as close to the code as
possible — grain, column descriptions, and tests in each layer's `schema.yml` /
model docs. If it's testable, it goes in the yml; if it's a judgment call among
alternatives, it goes here. The same rule should never be worded in both places.

Entries are numbered `M-N` and follow the README ADR format: decision,
alternatives considered, why, trade-offs.

---

## M-0. Grain: EIA stays hourly through intermediate; the cross-source mart is daily on `(ba, date)`

**Chosen:** `int_eia__generation_hourly` preserves the hourly grain `(ba, period)` —
aggregate late. The first cross-source mart is daily, grain `(ba, date)`: EIA rolled
up to the day joined to `int_noaa__weather_daily` (already `(ba, observation_date)`).

**Alternatives considered:**
- **Roll EIA up to daily in intermediate** — simpler mart join, but destroys the
  intraday shape (duck curve, evening peaks) that the hourly products and the
  forecast features need. Once aggregated, the hour is gone.
- **Hourly cross-source mart** — NOAA daily summaries have no intraday signal to
  contribute; an hourly weather join would just broadcast one value across 24 rows.

**Why:**
- Weather is daily, so `(ba, date)` is the natural cross-source grain — the finest
  grain at which both sources have real information.
- Keeping the hourly intermediate means daily is *derived*, not *destructive*; any
  future hourly product (price/LMP joins are hourly) builds from the same model.

**Trade-offs:**
- Two grains to document and test instead of one. The mart's rollup rules (sum vs
  mean per measure) are part of its spec.

---

## M-1. Renewable share: computed over gross-positive generation

**Chosen:** `renewable_share = renewable_gross_mwh / nullif(total_gross_mwh, 0)`
where every fuel value is clamped at zero before summing
(`greatest(generation_mwh, 0)`). Renewables = `SUN + WND + WAT` (unchanged).
Share is in `[0, 1]` by construction and tested as such
(`dbt_utils.accepted_range`).

**Alternatives considered:**
- **Net values (status quo)** — physically honest, but storage and pumping make the
  ratio unexplainable: the 2026-06-10 DQ pass found 49,259 BA-hours below 0 and
  7,611 above 1 (range −96.5 to 190) out of 3.96M, driven by hours where net total
  is near zero or negative. A share of 190 answers no analyst's question.
- **Null the share when total is below a threshold** — patches the explosions but
  introduces an arbitrary magic number and still leaves mildly-out-of-range values.

**Why:**
- Negative generation is *real* in EIA data, not an ingestion bug: pumped storage
  (`PS`, 34% of hours negative), batteries charging (`BAT`, 34%), solar station
  service at night (`SUN`, 8%). A *share of production* should be computed over
  production, not net flows.
- Gross-over-gross matches what "renewable share" means on every public dashboard,
  and makes the `[0, 1]` bound a true output assertion rather than a wish.

**Trade-offs:**
- The share ignores storage dynamics by design. Storage/charging analysis is a
  different question and gets its own model from staging (see M-2), not a variant
  of this metric.

---

## M-2. Gross and net totals: columns in one model, not separate tables

**Chosen:** `int_eia__generation_hourly` carries both `total_net_mwh` (honest sum,
negatives included — the demand-proxy use case) and `total_gross_mwh` (clamped —
the M-1 share denominator) as columns at the same `(ba, period)` grain.

**Alternatives considered:**
- **A second table with the clamped view of the world** — duplicated *grain*: two
  near-identical `(ba, period)` models that drift apart and force every consumer
  to ask "which one do I use?"

**Why:**
- No data loss either way: landing (`EIA.EIA_GRID`) and staging keep full net
  values at `(ba, period, fuel_type)`; these models are derived lenses. The choice
  is purely about ergonomics, and extra columns are near-free in a view while a
  duplicate-grain table is a permanent ambiguity.
- Any future storage-flow product is a *new model from staging* with its own grain,
  not a fork of this one.

**Trade-offs:**
- Consumers must pick the right total per use case; the column docs carry that
  guidance (`net` for demand trends, `gross` for share/mix).

---

## M-3. NOAA datatype policy: derive `tavg`, keep sparse datatypes null

**Chosen:** the modeled layers derive `tavg = (tmax + tmin) / 2` instead of using
the raw `TAVG` datatype, and sparse datatypes (`SNOW` 27.9% null, `RHAV`/`ASLP`/
`ADPT` ~18% null) stay **null** where stations don't report them — no imputation,
no coalesce-to-zero.

**Alternatives considered:**
- **Use raw `TAVG`** — it is 99.9% null in our station set (2026-06-10 DQ pass);
  carrying an effectively-absent column into a mart is a trap for every consumer.
- **Coalesce `SNOW` to 0** — tempting (no snow report usually means no snow), but
  it erases the distinction between "station doesn't measure snow" and "zero snow",
  and southern-station nulls are climate, not errors. Downstream features can make
  that call per use case; the mart shouldn't bake it in.

**Why:**
- `(tmax + tmin) / 2` is the standard climatological approximation and is computable
  for 99.9% of station-days — the exact inverse of raw `TAVG`'s coverage.
- Honest nulls preserve information; imputation is a *feature-engineering* decision
  that belongs to the consumer (e.g. the forecast feature store), not the mart.

**Trade-offs:**
- Derived `tavg` is an approximation (biased vs true hourly mean on skewed days).
- Consumers must handle nulls; the column docs state the expected null rates.

---

## M-4. Cross-source join: LEFT from generation; weather nullable in the lag window

**Chosen:** the daily mart joins **left** from the EIA daily rollup to
`int_noaa__weather_daily` on `(ba, date)`. Weather columns are nullable, expected
null for roughly the trailing 3 days (NOAA publication lag) and for the 57 BAs
without weather coverage.

**Alternatives considered:**
- **Inner join** — silently drops the three most recent days of *every* BA (the
  2026-06-10 DQ pass: 99.1% of `(ba, date)` pairs match, and the misses are almost
  exactly the 3-day lag × 14 BAs). The freshest rows are the ones a daily product
  looks at first; an inner join hides exactly those.
- **Restrict the mart to the 14 weather BAs** — throws away 57 BAs of generation
  analytics to satisfy a join; weather-dependent consumers can filter instead.

**Why:**
- Generation is the platform's heartbeat and is fresher than weather by design
  (hourly vs lagged daily); the mart's row existence should track generation.
- A nullable weather column is queryable truth ("no weather landed yet"); a missing
  row is indistinguishable from an ingestion failure.

**Trade-offs:**
- Freshness tests must be source-aware: generation columns near-current, weather
  columns tested with a ~5-day tolerance so the normal lag doesn't page anyone.
- Consumers averaging weather columns must expect nulls at the head of the series.
