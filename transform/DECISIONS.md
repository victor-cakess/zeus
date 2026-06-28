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
