# Modeling decisions (dbt layer)

Business-rule and modeling decision records for the `transform/` dbt project — the
domain-logic counterpart to the infra ADRs in the repo-root `ADR.md`.

**Division of labor:** this file records *decisions* (the choice, the rejected
alternatives, the why); the enforceable *contract* lives as close to the code as
possible — grain, column descriptions, and tests in each layer's `schema.yml` /
model docs. If it's testable, it goes in the yml; if it's a judgment call among
alternatives, it goes here. The same rule should never be worded in both places.

Entries are numbered `M-N` and follow the `ADR.md` ADR format: decision,
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

---

## M-5. Marts materialization: incremental hourly fact, plain-table daily mart

**Chosen:** `fct_generation_hourly` is **incremental** (merge on `(ba, period)`,
reprocessing a trailing 10-day window each run). Window derivation: a period keeps
receiving revised rows until it ages out of the 7-day ingestion lookback — an
8-calendar-day span with boundaries included — plus 2 days of operational slack
(a missed run fixed later, same-day re-runs). Too-small window = revisions land in
staging but never reach the fact, **silently and permanently**; too-big = a few
extra seconds of scan. The window is derived from the lookback — if the lookback
changes, this changes with it. `fct_energy_daily` is a plain **table**, rebuilt
each run from the hourly fact. Staging/intermediate stay views.

**Alternatives considered:**
- **Marts as views** — every consumer query recomputes the staging dedup window
  over the full landing table (24M+ rows); fine for the modeling layers,
  unacceptable as the repeated-consumption surface.
- **Full-refresh table for the hourly fact** — rebuild cost grows unbounded with
  history for data that is immutable past the revision window.
- **Incremental daily mart** — ~165K rows reading from an already-materialized
  fact; incremental machinery would be complexity without payoff.

**Why:**
- The hourly fact is the high-volume surface; incremental + merge handles the
  late-arriving revisions the lookback exists for, paying only for the window.
- The 10-day window is tied to the ingestion lookback — if the lookback changes,
  this window changes with it.

**Trade-offs:**
- Incremental models need `--full-refresh` discipline on schema/logic changes.
- The daily mart re-reads the whole fact each run; revisit if rebuild time grows.

---

## M-6. Daily renewable share: ratio of sums, not mean of hourly shares

**Chosen:** `fct_energy_daily.renewable_share =
sum(renewable_gross_mwh) / sum(total_gross_mwh)` over the day's hours.

**Alternatives considered:**
- **avg(hourly renewable_share)** — weights every hour equally, so a 3 AM hour
  with tiny total generation counts as much as the evening peak; the "daily share"
  would not equal renewable MWh over total MWh and would contradict the daily
  totals displayed next to it.

**Why:**
- Ratio of sums weights every MWh equally and is self-consistent: the share equals
  the ratio of the two daily totals in the same row.

**Trade-offs:**
- Consumers who naively average the hourly share will get a (slightly) different
  number than the mart's; the column doc states the rule.

---

## M-7. Daily grain day = UTC calendar day; weather joins station-local day as-is

**Chosen:** `fct_energy_daily.date` is the UTC calendar day of `period`
(`period::date`). NOAA weather joins on its station-local `observation_date`
unshifted — the ≤1-day boundary skew is documented, not corrected.

**Alternatives considered:**
- **Shift weather to UTC days** — impossible without sub-daily weather; NOAA
  daily summaries have no intraday resolution to re-bucket.
- **Roll generation up to BA-local days** — needs a per-BA timezone map + DST
  handling, and breaks cross-BA comparability ("a day" stops meaning one thing).

**Why:**
- EIA periods are UTC; the UTC day is unambiguous, DST-free, and identical across
  all 71 BAs.
- At daily grain the skew is within weather's natural autocorrelation (adjacent
  days are similar), and the dominant misalignment is NOAA's ~3-day publication
  lag (M-4) anyway.

**Trade-offs:**
- A BA-day's weather is the station-local day overlapping most of that UTC day,
  not an exact UTC window — fine for joins/features, not for hour-precise
  attribution (which daily weather can't support regardless).

**Empirical validation (2026-06-10):** periods confirmed UTC from the data itself —
summer solar peaks at the recorded hours 21 (CISO), 18 (ERCO), 17 (ISNE), exactly
each zone's local-afternoon peak expressed in UTC (a local-time encoding would peak
~13 everywhere); zero future-dated periods. Join signal is strong: ERCO summer-2025
corr(TMAX, daily gross MWh) = 0.76 same-day, 0.83 prior-day, 0.55 next-day. The
past>future asymmetry is physical (cooling load lags heat — thermal inertia) plus
the documented boundary skew; downstream forecast features should include lagged
weather, not just same-day.

---

## M-8. Physical-bounds tests at staging: surface bad sensor values, don't clean them

**Chosen:** every NOAA measure column with a physical interpretation carries a
`dbt_utils.accepted_range` output assertion in the staging `schema.yml`, bounds set
at physical-extreme plausibility (validated against the full history, 2026-06-10).
Out-of-range values are **surfaced, never mutated** — staging stays cleaning-only
per the layering rule. Columns with known sensor-garbage rows in history run at
`severity: warn`; the rest at the default `error`. (EIA staging also pins
`value_units` to its documented constant — a units contract, same
fail-loudly-at-staging principle.)

**Alternatives considered:**
- **QC-null rule in staging (null out-of-range values)** — a judgment call, which
  staging is forbidden by design; it also silently destroys evidence. If a QC rule
  ever lands it belongs in intermediate, at which point the warn tests upgrade to
  error (noted in the yml).
- **`severity: error` everywhere** — the known garbage rows are years old and
  already understood; failing every future build over them turns the dbt step (and
  the daily execution) permanently red for non-news.
- **No bounds tests (trust NCEI QC)** — the known garbage rows passed NCEI QC;
  without output assertions the next one reaches the mart unannounced.

**Why:**
- Bad sensor values are real in the data (a −72.7 °C Texas `tmin`), so plausibility
  is an output assertion worth enforcing, not an assumption.
- Warn severity reports the rows in every build without failing the pipeline —
  the right cost for known, low-volume, historical garbage.

**Trade-offs:**
- Warn results don't page anyone; a slow accumulation of new garbage rows would
  only be noticed by reading build output.
- Bounds wide enough for genuine extremes can't catch plausible-but-wrong values.
- The specific bounds and the warn-column list live in the yml (the contract);
  this entry records only the policy.

---

## M-9. FRED staging: dedup keep-latest, per-group bounds before the LOCF carry

**Chosen:** `stg_fred__prices` dedups landing on `(series, date)` keeping the latest
`ingestion_date` (the same lookback-overlap rule as EIA/NOAA), passes values through
in **native units**, and carries **per-group** physical-bounds output assertions in
its `schema.yml`. The 15 series fall into bound groups by unit/scale: the monthly
indexes (PPI coal/natgas/elec, CPI energy) and the always-positive price *levels*
(ELECPRICE `$/kWh`, retail GASOLINE/DIESEL `$/gal`) get a `> 0` floor + a generous
upper cap; the eight daily **spot** prices (WTI/BRENT `$/bbl`, HENRYHUB `$/MMBtu`,
and the `$/gal` product spots) get an upper sanity cap **only** — no lower bound,
because spot prices legitimately go negative (WTI −36.98 on 2020-04-20).

**Alternatives considered:**
- **A single blanket `value > 0`** — wrong: fails on the real negative WTI print.
- **A uniform sanity cap, no floor anywhere** — wouldn't catch a negative index or
  retail value, which is physically impossible and signals corruption.

**Why:**
- Bounds belong at staging (M-8 — surface bad values, don't clean them), and here
  there's an extra reason to test *before* the intermediate: `int_fred` LOCF carries
  a value forward across many days, so one garbage observation would propagate.
  Catching it on the source row keeps the blast radius to one `(series, date)`.
- Per-group because the series span four unit scales (`$/bbl`, `$/gal`, `$/MMBtu`,
  index) with different plausible ranges — one bound can't fit all.

**Trade-offs:**
- Long staging holds every series in one `value` column, so the bounds tests scope
  by series with a `where:` clause (≈6 grouped tests) rather than per-column ranges
  like wide NOAA staging. The specific bounds live in the yml (the contract); this
  entry records only the grouping policy.
- Upper caps are wide enough for genuine spikes (Henry Hub hit 30.72 in Winter Storm
  Uri) and so can't catch plausible-but-wrong mid-range values.

**Open data-quality flag (BRENT min, 2026-06-11):** the 2014–2026 BRENT minimum is
$9.12/bbl — below any known post-2014 Brent floor (the COVID low was ~$16). Because
the `$/bbl` spot bound is **max-only** by this decision, the test does **not** catch
it; LOCF carries it at most one day (daily series). Unresolved — real outlier vs bad
FRED print is TBD. Check the observation date (`select date, value from
ZEUS_DEV.FRED.FRED_GRID where series = 'BRENT' and value < 15 order by value`) before
relying on BRENT for analysis. If it proves to be a bad print, the fix is upstream
(the client/source), not a staging clean (M-8).

---

## M-10. Mixed frequency reshaped onto a daily LOCF spine, wide

**Chosen:** `int_fred__prices_daily` reshapes the mixed-frequency series (8
daily-business-day, 2 weekly, 5 monthly) onto a single **daily calendar spine** —
one row per date, one column per series (**wide**). Each series is
last-observation-carried-forward (**LOCF**) across the gaps its native frequency
leaves: weekends/holidays for daily spots, the 6 intra-week days for weekly retail,
the ~30 intra-month days for monthly indexes. Carry-forward applies **everywhere**,
including the **trailing** publication-lag window (a series' latest value is held
until its next release lands). The **leading** edge — dates before a series' first
real observation — stays null (no value to carry). The spine runs from the earliest
observation across all series to `current_date`.

**Alternatives considered:**
- **Keep the native sparse grain and let each consumer densify** — pushes the same
  LOCF logic into every consumer, and a 7-day-a-week grid product would have no
  price on weekends.
- **Null the trailing lag window like NOAA weather (M-4)** — reintroduces a join-hole
  on the freshest, most-queried rows and creates a third missingness state; rejected
  because the staleness columns (M-11) already label carried-forward values, so
  nulling adds only holes.
- **Long format (series/date/value rows)** — compact, but every date-join needs a
  pivot and you lose per-series column contracts; wide matches
  `int_noaa__weather_daily` and makes the downstream join a plain equi-join on date.

**Why:**
- The grid and weather run every calendar day and join on date; a dense daily price
  for every series is what those consumers need.
- LOCF is the standard way to value a lower-frequency series on an off day — the last
  published price *is* the prevailing price until the next print.
- No staleness cap: capping would null mid-series and break the join the spine exists
  to enable; instead staleness is a column (M-11) so consumers cap themselves.
  Expected max staleness is bounded by frequency (≈3 days daily, ≈7 weekly, ≈31
  monthly, up to ~120 for a monthly series mid-revision-lag) and documented per column.

**Trade-offs:**
- A carried-forward value looks identical to a fresh one in the `value` column alone —
  the `is_observed`/`staleness` companions (M-11) are mandatory, not optional, to
  recover that.
- Wide means 15 series × 3 columns; a Jinja loop over the series list generates them
  to keep the model DRY (diverging from NOAA's hand-listed columns, justified by the
  45-column count). Values are latest-revision (M-11).

---

## M-11. Missingness as signal — `is_observed` + `staleness_days`; point-in-time deferred

**Chosen:** alongside each ffilled value, `int_fred` carries `<series>_is_observed`
(boolean — was this date a real observation or a carry-forward) and
`<series>_staleness_days` (integer — days since the last real observation; 0 on an
observed day, null before the first). One table serves BI (read the value) and ML
(read value + how stale + whether real) without a second model. The values are the
**latest revision** of each observation, **not** point-in-time as-of values;
reconstructing as-of snapshots (what was known on date D) is **deferred** — the
landing table's full `ingestion_date` revision history supports building it later when
a forecasting use case needs leakage-free training features.

**Alternatives considered:**
- **`staleness_days` only** (`is_observed ≡ staleness == 0`, so derivable) — rejected
  for consumer ergonomics; an explicit boolean reads cleaner as a filter/feature than
  an equality check, and the redundancy is two cheap columns.
- **A separate ML-feature table** — premature; the flags are cheap enough to live in
  the one shared model.
- **Build point-in-time now** — a large feature-store effort for no current consumer;
  the revision history is preserved in landing, so the option stays open.

**Why:**
- Plain LOCF erases the difference between a fresh print and a month-old
  carry-forward, and that difference is signal a model should see (and a caveat an
  analyst should know). Exposing it as columns keeps a single source of truth.
- The point-in-time deferral is *recorded* rather than silently ignored because
  latest-revision values are forward-looking leakage for ML training (a 2020 PPI row
  reflects a revision published months later) — a real correctness issue parked
  deliberately, not missed.

**Trade-offs:**
- 30 companion columns on a 15-series table; `is_observed` is redundant with
  `staleness_days` by construction.
- Consumers training models on this table without the deferred as-of layer must
  understand the leakage caveat (documented on the model).

---

## M-12. FRED mart standalone, full-rebuild table, not joined into `fct_energy_daily`

**Chosen:** `fct_fuel_prices_daily` is a standalone **date-grain** mart (one row per
date — the wide intermediate materialized as a plain **table**, full-rebuilt each
run). It is **not** joined into `fct_energy_daily`: FRED is national (no `ba`), so
embedding it would repeat every price across all 71 BAs. Consumers that want prices
beside generation join the two marts on `date` themselves; an embedded view can come
later if an analysis demands it.

**Alternatives considered:**
- **Incremental merge like `fct_generation_hourly` (M-5)** — buys nothing and is
  actively wrong: BLS revises monthly PPI/CPI up to ~4 months back (rewriting old
  rows) and the LOCF output shifts every day as the spine extends, so a bounded
  incremental window can't capture the changes; the table is tiny (~4,500 date rows),
  so a full rebuild is trivial.
- **Join into `fct_energy_daily` now** — 71× row fan-out of identical national values,
  for a cross-source join no consumer has asked for yet.

**Why:**
- M-5's rule — marts are the materialized consumption surface so consumers don't
  recompute the view chain over landing. The daily-mart half of M-5 (plain table,
  full rebuild) fits exactly; the incremental half was for the high-volume hourly
  fact, which this is not.
- Standalone keeps the grain honest (`date`, not `ba × date`) until a real
  cross-source need defines how prices should attach.

**Trade-offs:**
- A price-vs-generation analysis writes its own join rather than reading one wide
  table. Revisit the embed-vs-join-yourself call when such a consumer appears.

---

## M-13. Reporting layer: 1:1 pass-through views as a governance boundary, not a transform

**Chosen:** A fourth dbt layer, `reporting` (`ZEUS_DEV.REPORTING`, views), sits above
marts: one view per mart (`vw_energy_daily`, `vw_generation_hourly`,
`vw_fuel_prices_daily`), each a **1:1 pass-through** of its mart via `ref()` with **no
logic** — same grain, same columns. It carries **no tests** (the marts already enforce
the not_null/unique/accepted_range contracts; re-testing a pass-through is redundant
cost every build) and **exposes all mart columns**, including FRED's `_is_observed` /
`_staleness_days` companions (M-11). It exists for **access governance, not modeling**:
it's the only schema the public dashboard's least-privilege role can `SELECT`, so the
marts/intermediate/staging/landing stay invisible to an internet-facing credential.
The access half (role, warehouse, resource monitor, grants) lives in `ADR.md` #17.

**Alternatives considered:**
- **Point the dashboard at the marts directly** — no extra layer, but then the public
  role needs `SELECT` on `MARTS`, coupling the public surface to physical mart changes
  and exposing the whole consumption schema. A thin view layer gives a stable,
  curated, separately-grantable contract.
- **Curate columns now** (expose only what the dashboard currently charts) — tighter,
  but the dashboard is still evolving; trimming would mean editing a view on every new
  chart. Full pass-through preserves flexibility; tighten once the dashboard stabilizes.
- **Add tests on the views** — duplicates the marts' contracts for no new coverage.

**Why:**
- Keeps the marts as the internal consumption surface (M-5) while giving *external*
  consumers a deliberately narrow, governed door — the view layer is the seam where
  "what we model" meets "what a public app may read."
- Views are free to keep fresh (rebuilt every `dbt build`) and `ref()`-linked, so the
  serving surface can't drift from the marts.

**Trade-offs:**
- A genuinely useful column hidden behind `select <cols>` (the two facts) needs a
  one-line view edit to surface — the cost of an explicit contract over `select *`.
- Another layer to keep in mind when reasoning about lineage, though it adds zero
  business logic.

---

## M-14. EIA region data pivots to wide at intermediate; nulls stay null

**Chosen:** `int_eia_region__demand_hourly` reshapes the staging narrow grain
`(ba, period, metric)` to one row per `(ba, period)` with the four metrics as
columns: `demand_mwh` (D), `demand_forecast_mwh` (DF), `net_generation_mwh` (NG),
`total_interchange_mwh` (TI). Missing metrics stay **null** — generation-only BAs
(e.g. YAD) never report D/DF; that absence is real data shape, not a defect to
impute or filter. Hourly grain preserved (aggregate late, M-0). `TI` keeps EIA's
sign convention: positive = net exports.

**Alternatives considered:**
- **Stay narrow through the marts** — every consumer (accuracy metrics, energy
  balance, ML features) needs D and DF side by side; narrow forces the same
  self-join/pivot into every downstream query.
- **Pivot in staging** — staging is cleaning only (dedup + rename); a reshape is a
  judgment call and belongs in intermediate by the layering contract.
- **Fill missing D/DF with 0 or drop those BAs** — fabricates demand where none is
  reported, and hides which BAs are demand-reporting (a fact downstream models
  key on).

**Why:**
- One pivot, written once, materialized once (M-16) — consumers read columns.
- Null-preservation keeps "which BAs report demand" answerable from the model
  itself (M-11 spirit: missingness is signal).

**Trade-offs:**
- A future fifth region-data metric is a model change (new column), not free the
  way a narrow schema would make it. The route has published exactly these four
  for years; taking the ergonomics today.

---

## M-15. Forecast accuracy: long grain with a forecaster dimension; WAPE primary

**Chosen:** `fct_demand_accuracy` grain is `(ba, date, forecaster)` — **long**.
Today the single forecaster is `eia_df` (the operators' own day-ahead demand
forecast); Phase 4's ML loop adds `naive` and `zeus_v<N>` as **rows**, with zero
schema change. Metrics per BA-day, computed only over hours where both demand and
forecast are present (`hours_scored` counts them; BA-days with nothing to score
emit no row):
- `wape` = Σ|D−DF| / Σ|D| — **primary**: ratio of the day's sums (M-6 spirit),
  robust to near-zero-demand hours.
- `bias_pct` = Σ(DF−D) / Σ|D| — signed; positive = systematic over-forecasting.
- `mape` — secondary, kept for familiarity: mean of hourly |D−DF|/|D| over D≠0
  hours; near-zero-demand hours inflate it, which is exactly why WAPE is primary.

**Alternatives considered:**
- **Wide grain `(ba, date)` with per-forecaster columns** — simpler today, but
  every new forecaster is a schema migration and an incremental-CI headache;
  the roadmap's Phase 4 makes new forecasters a certainty, not a maybe.
- **MAPE as primary** — the textbook default, but hourly demand crosses near zero
  for small/storage-heavy BAs, where MAPE explodes; WAPE degrades gracefully.
- **Scoring all hours, treating missing DF as error** — conflates "operator
  didn't publish a forecast" with "operator forecast badly".

**Why:**
- Long is Phase-4-proof: the scorecard becomes the shared arena by INSERT, not
  ALTER.
- Ratio-of-sums daily metrics follow the established M-6 rule.

**Trade-offs:**
- Single-forecaster queries need a `where forecaster = 'eia_df'` filter today.
- `accepted_values` on `forecaster` must be extended when Phase 4 adds rows —
  deliberate: a new forecaster should be a conscious contract change.

---

## M-16. Phase-3 materialization: M-5 applied; no Snowflake MVs or dynamic tables

**Chosen:** the roadmap's materialization policy, applied to the Phase-3 models
as instances of M-5 — `fct_demand_hourly` is **incremental** (merge on
`(ba, period)`, trailing 10-day window: eia_region shares the 7-day ingestion
lookback, so the same 8-day revision span + 2 days slack applies);
`fct_demand_accuracy` is a **plain table** rebuilt from the materialized hourly
fact; the intermediate pivot stays a view. Also recording the standing rejection:
**no Snowflake materialized views, no dynamic tables** anywhere in the project.

**Alternatives considered:**
- **Snowflake materialized views** — can't express the staging dedup or the pivot
  (no window functions; Enterprise-only feature).
- **Dynamic tables** — mechanically workable, but `TARGET_LAG` is a second
  freshness scheduler competing with the state machine; the platform's core
  design is one orchestrator owning "when things run", and refresh timing must
  not leak out of it.
- **Incremental accuracy mart** — a small daily-grain aggregate over an
  already-materialized fact; incremental machinery without payoff (M-5's
  daily-mart argument verbatim).

**Why:**
- Same revision horizon ⇒ same window arithmetic; one rule to remember, derived
  from the lookback in both places.
- dbt-owned materialization keeps every refresh visible in the run report and
  the digest — a dynamic table refreshing on its own clock would not be.

**Trade-offs:**
- `--full-refresh` discipline for `fct_demand_hourly` on schema/logic changes
  (same as M-5).
- If the eia_region lookback ever diverges from EIA's 7 days, the 10-day window
  here must be revisited independently.

---

## M-17. Degree days: base 65°F, °F-days, computed over the BA-mean tavg

**Chosen:** `hdd` / `cdd` in `int_noaa__weather_daily` (passed through to
`fct_energy_daily`, nullable per M-4): `hdd = max(65 − tavg°F, 0)`,
`cdd = max(tavg°F − 65, 0)`, in **°F-days** with the **65°F base** — the US
industry/EIA/NOAA convention — converting our °C `tavg` inline. Computed over
the **derived BA-mean tavg** (M-3's `(tmax+tmin)/2`), which is exactly the
daily-mean definition NOAA's official degree days use. Degree days **of the
mean**, not the mean of per-station degree days.

**Alternatives considered:**
- **Base 18°C / °C-days** (the European convention) — the demand data is US
  BAs; every published US benchmark (EIA, NOAA CPC) is 65°F-based, so °F-days
  keep our numbers directly comparable.
- **Mean of per-station degree days** — by Jensen's inequality DD(mean) ≤
  mean(DD) when stations straddle the base, so per-station-then-average is
  arguably more physical; but it breaks consistency with every other weather
  column (all BA-means, M-3) and with how consumers will sanity-check against
  published BA-level figures.
- **Hourly degree hours from EIA-region temps** — no hourly temperature source
  ingested; NOAA daily summaries are the platform's weather truth.

**Why:**
- Comparable to published US degree-day data with zero adjustment.
- One convention (M-3 BA-mean) carried through; the derivation lives in the
  intermediate layer with every other weather judgment call.

**Trade-offs:**
- Slight understatement vs per-station degree days on days when a BA's
  stations straddle 65°F (Jensen). Accepted for consistency; revisit only if
  demand-response modeling (Phase 4) shows it matters.
- °F-days beside °C temperature columns is a mixed-unit surface — documented
  per column in the yml.

---

## M-18. Energy-balance test: daily grain, dual tolerance, warn — a finding, not a filter

**Chosen:** a custom generic test (`energy_balance`, on `fct_demand_hourly`)
asserting the EIA-930 identity `D = NG − TI` (TI positive = net exports) at
**(ba, day)** grain, breaching only when the daily residual clears **both**
tolerances: **> 5% of the day's demand AND > 500 MWh**. Severity **warn** —
M-8's surface-don't-clean policy extended cross-metric: a breach is a
reporting-quality finding about the operator, never a row to fix or drop.

Calibration (data look, 2026-07): the sign convention is confirmed by the data
— 62% of 4.2M hourly rows satisfy `D = NG − TI` **exactly**. Hourly residuals
carry metering-clock noise between the three independently reported series;
daily sums absorb it (BA-day relative residual p90 ≈ 2.1%, under the 5% bar).
The expected baseline at these tolerances is **~9.9k breaching BA-days across
45 BAs (of 175k)**, dominated by BPAT and NW (~85% of their days — a chronic,
structural mismatch in what Bonneville-area respondents report as demand, not
an ingestion bug). The warn count is therefore a *characterization* with a
known baseline; material drift from it, or a new BA appearing, is the signal.

**Alternatives considered:**
- **Hourly grain** — flags ~250k rows, most of it clock-boundary noise; the
  physics claim is about energy over a period, not instantaneous alignment.
- **Single tolerance** — relative-only flags trivial MWh on tiny BAs;
  absolute-only flags proportionally meaningless gaps on PJM-sized BAs.
- **Error severity / excluding chronic BAs** — turns a data-quality finding
  into a build failure (or hides it); the landing layer is truth-as-received.
- **Trailing-window test** (only recent days) — quieter, but silently forgets
  the historical finding; the full-history warn keeps the platform honest.

**Why:**
- The test encodes the physics; the tolerances encode the measured noise floor;
  the severity encodes the policy. Each is independently adjustable.
- BPAT/NW chronic imbalance is exactly the kind of finding the analytics layer
  exists to surface (and a candidate dashboard exhibit for 3d).

**Trade-offs:**
- A perpetual ~9.9k-row warn: consumers must read the count against the
  recorded baseline rather than expecting zero. Revisit if it proves noisy —
  e.g. split into a tight anomaly test (error) + a monitored view (finding).

---

## M-19. Generation-by-fuel: a daily gross-MWh mart, long by fuel type

**Chosen:** a dedicated daily path — `int_eia__generation_by_fuel_daily` →
`fct_generation_by_fuel_daily` → `vw_generation_by_fuel_daily` — at grain
`(ba, date, fuel_type)`, carrying **gross MWh** per fuel (each fuel clamped at 0
before summing, M-1/M-2). Long, not wide: one row per fuel, so a new EIA-930 code
appears as rows, never a schema change. Feeds the dashboard's daily generation-mix
stacked area (Generation tab).

**Alternatives considered:**
- **Widen `fct_energy_daily` with fuel columns** — a `coal_mwh, gas_mwh, …` block
  bolted onto the cross-source mart. Freezes the fuel list into the schema (a new
  code = a migration), bloats a mart whose grain is `(ba, date)` with source-specific
  detail, and mixes a single-source breakdown into the weather/demand join.
- **Read fuel detail straight from staging in the dashboard** — impossible by design:
  the dashboard role holds SELECT on `REPORTING.*` only (the governance boundary). The
  data must reach a reporting view or the app physically cannot see it.
- **Net MWh** — lets storage-charging / station-service hours go negative, which a
  stacked mix can't represent. Gross = production *from that source* is the honest
  per-fuel quantity, consistent with `renewable_gross_mwh` (M-1).
- **Hourly grain** — a fuel-resolved duck curve. Deferred: the dashboard's default
  range is up to ~2 years, where daily is the readable grain; the hourly shape stays
  recoverable from staging if a later product needs it (M-0 aggregate-late spirit).

**Why:**
- The fuel mix is a first-class question ("what runs this grid, and how is that
  changing"), so it earns its own model rather than riding on the cross-source mart.
- Long-by-fuel mirrors `fct_demand_accuracy` (long by forecaster, M-15): new
  categories are data, not DDL.
- Gross + daily reuse conventions already set on the generation path (M-1/M-2, M-0),
  so nothing new to reason about downstream.

**Trade-offs:**
- A second pass over `stg_eia__generation`: the incremental `fct_generation_hourly`
  exists to avoid re-scanning landing, and this daily mart re-scans it once per build.
  Accepted — the output is small (~71 BA × ~8 fuels × days) and the XS warehouse
  absorbs one more staging scan. Escalation if build time bites: make the mart
  incremental on a trailing window, like `fct_generation_hourly`.
- Gross-only means this mart can't answer net-load / storage questions; those stay on
  `fct_energy_daily.total_net_mwh`.
- The mart keeps all 16 raw EIA-930 codes (COL/NG/NUC/OIL/WAT/SUN/WND/OTH plus storage
  variants BAT/PS/SNB/WNB/OES/UES, GEO, UNK). Collapsing them into ~9 recognizable
  buckets (Solar = SUN + SNB, Storage = battery + pumped-storage discharge, …) is a
  **presentation** choice that lives in the dashboard, not the mart — the taxonomy can
  change without a data migration, and the granular truth stays queryable.

---

## M-20. QC bounds land in intermediate: null out-of-range values, respondent-aware ceiling

**Chosen:** the QC-null rule M-8 anticipated now lands in the **intermediate** layer for
EIA (`int_eia__generation_hourly`, `int_eia__generation_by_fuel_daily`), EIA region
(`int_eia_region__demand_hourly`), and NOAA (`int_noaa__weather_daily`). Physically-
impossible values are set to **NULL** before any `SUM`/`AVG`/`MAX`; the row and its other
columns survive. The ceiling is **respondent-aware**: `US48` (the EIA Lower-48 national
aggregate, which runs ~10x a single BA) gets a 1,000,000 MWh/hour ceiling, every other
respondent 300,000; demand and day-ahead forecast (D/DF) are additionally floored at 0
(demand can't be negative), while generation and interchange stay signed (magnitude bound
only). NOAA reuses the physical ranges the staging `schema.yml` already asserts (M-8).
The bound **numbers** live in the model SQL + the yml tests (the contract); this entry
records the policy. Enforcement is an `error`-level `accepted_range` on the **intermediate**
output; the staging bounds tests stay `warn` (surfacing).

**Alternatives considered:**
- **Interpolate the gap (mean of neighbouring hours/days)** — fabricates a value that then
  reads as a real observation on the dashboard, contradicting the honest-nulls principle
  (M-3, M-11), and needs LAG/LEAD with edge cases (first/last row, consecutive bad values,
  neighbour also out of range). Every downstream rollup ignores NULLs, so nulling costs a
  daily mean at most one of ~24 hours — not worth the fabrication.
- **Drop the whole row** — wrong grain for the wide NOAA model (one bad `tmax` would discard
  the row's good `prcp`/wind) and the EIA-region pivot; also shrinks the grid instead of
  marking the gap. Per-value nulling is the right granularity.
- **A flat, respondent-blind ceiling** — rejected after empirical validation (2026-07-11):
  `US48` demand legitimately reaches ~776k and net generation similar, and EIA-930 region
  aggregates (MIDA ~224k, MIDW, TEX, …) sit well above a single BA. A flat 250–300k cap
  would have nulled ~66k legitimate US48 rows. Every *non-US48* value over 300k, by contrast,
  was garbage (2^31 sentinels, a 465-row BANC bad-data run at ~3.3M against a ~4k real scale,
  FMPP rows ~90x its size), so one exemption cleanly separates garbage from truth.
- **Clean in staging (the single chokepoint)** — forbidden by the layering rule: a bounds
  threshold is a judgment call, and staging is zero-judgment (M-8). Cleaning belongs here.

**Why:**
- Impossible values (a single-BA hour of 3.3M MWh, −72.7 °C) blow out every mart's min/max
  and any chart axis; nulling them at the first post-staging layer protects all consumers
  while landing/staging keep the raw truth (append-only, M-8).
- Respondent-aware with a single `US48` exemption is the simplest rule that never nulls a
  legitimate value — the paramount constraint — while still catching the moderate garbage a
  high flat cap would miss.
- `error`-level `accepted_range` on the cleaned intermediate output turns "the filter worked"
  into an enforced, self-checking contract; a future regression fails the build.

**Trade-offs:**
- **Borderline garbage below the ceiling survives**, by construction: a non-US48 value in
  (its real scale, 300k] is not caught (e.g. a lone ~250k single-fuel BA hour). The staging
  `warn` tests still surface these; tightening would risk nulling legitimate data, so the
  line is deliberate. A per-respondent relative bound (e.g. > N× that respondent's median)
  would catch them but adds window-function complexity and opacity — deferred.
- **`US48`'s own ceiling is loose** (1M): a US48-scale sentinel between 776k and 1M would pass.
  US48 has no garbage today; revisit if that changes.
- **`station_count` in `int_noaa__weather_daily`** counts a station present in the row even
  when a specific datatype was nulled, so it can slightly overstate the backing for that
  datatype (noted in the model).
- M-8's note said the staging `warn` tests would "upgrade to error" once a QC rule landed;
  in practice staging stays raw (its own no-mutation rule), so the `error` assertion lives on
  the cleaned **intermediate** output instead, and staging tests remain `warn` (surfacing).
- The `US48`-exemption ceiling is duplicated across the three EIA models (one CASE each);
  accepted over a shared macro/model per the project's inline-clamp idiom (M-1/M-2).

---

## M-21. Interchange fact: staging materialized directly, no intermediate layer

**Chosen:** `fct_interchange_hourly` is `stg_eia_interchange__flows` materialized
incrementally (merge on `(period, fromba, toba)`, trailing 10-day window — the M-5
pattern), read by every consumer. There is **no** `int_eia_interchange__*` view: the
staging output is already the consumption grain, and the fact adds materialization
only. QC-null bounds (M-20) are deferred: the staging `warn` bounds test
(±50k MWh) has to actually fire on real data before an intermediate cleaning layer
earns its existence.

**Alternatives considered:**
- **A pass-through `int_` view between stg and fact** (the eia/eia_region shape) —
  empty ceremony: the int layer exists for business rules and grain changes, and
  interchange has neither between staging and the fact.
- **A view instead of an incremental fact** — every Flows-tab query would re-dedup
  the ~23M-row landing table (the exact anti-pattern M-5 exists to prevent).

**Why:** the layering rule assigns *jobs*, not mandatory hops — staging cleans,
intermediate judges, marts materialize and serve. When a source needs no judgment,
inserting an empty layer just adds a name to maintain.

**Trade-offs:** if interchange ever needs a QC-null rule (M-20 style), it lands as a
new int model and the fact re-points — a `--full-refresh` migration, deliberate and
cheap at this volume.

---

## M-22. Interchange grain is directed; net position uses own reports only

**Chosen:** the fact keeps one **directed** row per `(period, fromba, toba)` — both
directions of a physical pair land as distinct rows, each the reporting BA's own
view of the same flow (positive `flow_mwh` = fromba exports). Reconciling the two
views is a *test*, not a filter: the `interchange_asymmetry` generic test groups by
`(day, least(fromba,toba), greatest(fromba,toba))` and sums signed flows — a
consistent pair sums to ~0 — flagging pairs that breach BOTH a relative (5% of the
pair's gross flow) and an absolute (500 MWh/day) tolerance, `severity: warn`
(M-8/M-18 policy: a reporting-quality finding, not a bug to clean). One-sided pairs
(counterparties outside the fan-out list) are excluded — that residual is coverage,
not asymmetry. `fct_net_position_hourly` rolls up to `(ba, period)` using each BA's
**own reports only** (`fromba = ba`): `exports_mwh` (positive flows), `imports_mwh`
(negative flows, sign-flipped), `net_position_mwh` (signed sum, positive = net
exporter — the same sign convention as eia_region's TI).

**Alternatives considered:**
- **Dedupe to one row per undirected pair at staging/fact** — destroys the second
  report, which is exactly the data the asymmetry test needs; also forces an
  arbitrary "which report wins" judgment into staging (forbidden, M-8).
- **Asymmetry test as a self-join on `(period, A, B)`** — a 23M×23M join re-run
  every build, and each violation reports twice (A,B) and (B,A). The canonical-pair
  GROUP BY is one scan and reports once.
- **Net position averaging the two views of each flow** — hides the asymmetry the
  test exists to surface, and makes a BA's net position depend on neighbors'
  reporting quality.

**Why:** both-directions-landing is the phase's analytical point (score the
operators against each other); daily grain absorbs hourly metering-clock noise
between independently reported series (M-18 precedent); dual tolerance keeps big
interties and small taps on the same rule.

**Trade-offs:** tolerances (5% / 500 MWh) validated against the first real data
(2026-07-12, Jan 2026 + trailing week): **795 of 5,126 pair-days breach (15.5%)** —
chronic offenders MISO↔PJM (every day, ~34k MWh/day mean residual; the two
operators agree on direction but not magnitude — likely differing pseudo-tie
treatment), BPAT↔PGE, MIDA↔MIDW. A real EIA-930 reporting finding, the M-18
energy-balance story repeated cross-pair. Net position covers only fan-out BAs (a
Canadian BA's net position is not computable from toba appearances alone —
deliberate).

---

## M-23. BA centroids: a hand-curated seed, joined in the dashboard, never in marts

**Chosen:** `seeds/ba_centroids.csv` — one row per **physical** balancing authority
appearing in the interchange facets (75 codes, incl. Canadian/Mexican counterparties
that are never a fromba), with hand-curated approximate footprint/operational-center
coordinates. Tested: `ba` unique/not-null, coordinates inside tight North-America
bounds (lat 24–60, lon −135–−60) at `error` severity — a typo should fail the build,
not draw an arc into the ocean. Served 1:1 via `vw_ba_centroids` (M-13); the
dashboard joins centroids against both arc ends **in pandas**. EIA regional
aggregates (US48, CAL, TEX, CAN, MEX, …) are deliberately absent: they are rollups
of the physical rows and would double-draw flows on a map. The flow map renders
rows with `flow_mwh > 0` — one arc per physical flow, exporter→importer; a
doubly-positive pair renders two arcs, which is the asymmetry finding made visible.

**Alternatives considered:**
- **Centroids computed from the EIA/HIFLD control-area shapefile** — more precise,
  but adds a geopandas one-off pipeline + BA-code matching for coordinates whose
  only job is anchoring map arcs; approximate centers are visually identical at
  continental zoom.
- **Joining centroids into `fct_interchange_hourly` / `fct_net_position_hourly`**
  (the ROADMAP draft) — four float columns × 23M rows to serve a 75-row lookup, and
  the arc map needs BOTH ends' coordinates anyway, so the mart join wouldn't even
  spare the dashboard its merge. Reporting views stay strict 1:1 (M-13).
- **An "international" bucket instead of real foreign centroids** — cheaper to
  curate but collapses HQT/IESO/BCHA/AESO/MHEB/SPC/NBSO/CEN arcs into one fake
  point; cross-border flows are among the most interesting on the map.

**Why:** the seed is reference data with a testable contract (dbt's exact job for
static CSVs); pandas-joining a 75-row table against a one-day slice is presentation
logic, not modeling; keeping aggregates out keeps every map/heatmap/bar consumer
from double-counting without per-consumer filters.

**Trade-offs:** coordinates are representative, not authoritative — documented in
the seed yml. New counterparty codes appear as `relationships` `warn`s on the fact
(coverage drift surfaces without failing the build; the 16 known aggregates are
scoped out of the test so the signal stays meaningful) and their arcs drop with a
caption count until curated. The seed carries codes beyond the current fan-out list
(e.g. `AEC`, `SPA`, `WACM`) so toba-side arcs and any future fan-out widening need
no seed change. The seed also doubles as the **physical-BA registry**: aggregates
DO report interchange (verified 2026-07-12 — they pair only with other aggregates,
never with physical BAs), they land like everything else (append-only truth, the
eia_region US48 precedent), and consumers wanting physical flows semi-join this
seed — the dashboard's bar/heatmap do exactly that.
