"""Zeus demo dashboard — a thin, read-only consumption layer over the dbt marts.

Standalone Streamlit app, intentionally NOT a project dependency (same stance as
docs/architecture.py): pulled in on demand via `uv run --with`, so it never ships
in any Lambda. Connects out to Snowflake as the least-privilege ZEUS_DEV_DASHBOARD
service user — SELECT on the REPORTING views only (never the marts or landing), on a
dedicated XS warehouse capped by a resource monitor. It writes nothing.

Run locally (from repo root) — key from a file; --with-requirements keeps local
runs on the SAME pinned versions Community Cloud installs (see requirements.txt):
    export SNOWFLAKE_ACCOUNT=<org>-<account>
    export SNOWFLAKE_PRIVATE_KEY_FILE="$(pwd)/sf_dashboard.p8"
    uv run --with-requirements dashboard/requirements.txt --no-project \
        streamlit run dashboard/app.py

On Streamlit Community Cloud the account + PEM private key come from st.secrets
(SNOWFLAKE_ACCOUNT, SNOWFLAKE_PRIVATE_KEY); user/role/warehouse default to the
dashboard identity below, so nothing else is required. See RUNBOOK.md → dashboard.
"""

import os

import altair as alt
import numpy as np
import pandas as pd
import pydeck as pdk
import snowflake.connector
import streamlit as st
from cryptography.hazmat.primitives import serialization

st.set_page_config(page_title="Zeus — energy data", layout="wide")

# Altair caps embedded data at 5000 rows by default; the generation-mix stacked area over
# a multi-year range × ~8 fuels clears that (e.g. 2 years ≈ 5900 rows). Our datasets are
# all small and bounded (one BA × date range), so lift the guard rather than sample.
alt.data_transformers.disable_max_rows()


def _setting(name: str) -> str | None:
    """Config value from an env var (local dev) or st.secrets (Community Cloud).

    Accessing st.secrets with no secrets.toml raises, so the lookup is guarded — local
    runs simply fall back to the environment.
    """
    if name in os.environ:
        return os.environ[name]
    try:
        return st.secrets.get(name)
    except Exception:
        return None


def _private_key_der() -> bytes:
    """Load the PKCS8 PEM key (key-pair auth) → DER bytes for the connector.

    Two sources, in priority order: a PEM string in st.secrets["SNOWFLAKE_PRIVATE_KEY"]
    (Community Cloud), else the file at $SNOWFLAKE_PRIVATE_KEY_FILE (local dev).
    """
    pem = _setting("SNOWFLAKE_PRIVATE_KEY")
    if pem:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    else:
        with open(os.environ["SNOWFLAKE_PRIVATE_KEY_FILE"], "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@st.cache_resource
def _connection():
    return snowflake.connector.connect(
        account=_setting("SNOWFLAKE_ACCOUNT"),
        user=_setting("SNOWFLAKE_USER") or "ZEUS_DEV_DASHBOARD",
        role=_setting("SNOWFLAKE_ROLE") or "ZEUS_DEV_DASHBOARD_ROLE",
        private_key=_private_key_der(),
        warehouse=_setting("SNOWFLAKE_WAREHOUSE") or "ZEUS_DEV_DASHBOARD_WH",
        database=_setting("SNOWFLAKE_DATABASE") or "ZEUS_DEV",
        schema="REPORTING",
        # heartbeat the session so it survives idle gaps while the process is
        # awake; the expired-session retry in query() covers process suspension
        client_session_keep_alive=True,
    )


# Snowflake session errors that mean "the cached connection's tokens are dead —
# reconnect": 390111 renewal failed, 390112 session expired, 390114 master
# token expired. The Community Cloud process outlives the ~4 h master token
# whenever the app sits idle, so the first visitor after a gap hits these.
_SESSION_EXPIRED_ERRNOS = {390111, 390112, 390114}


def _cursor():
    conn = _connection()
    if conn.is_closed():
        _connection.clear()
        conn = _connection()
    return conn.cursor()


@st.cache_data(ttl=600)
def query(sql: str) -> pd.DataFrame:
    """Run a read query, return a lowercase-columned DataFrame. Cached 10 min.

    Retries once on an expired/closed cached session (see _SESSION_EXPIRED_ERRNOS):
    drop the cached connection, reconnect, re-run. Any other error propagates.
    """
    try:
        cur = _cursor()
        cur.execute(sql)
    except snowflake.connector.errors.Error as e:
        if getattr(e, "errno", None) not in _SESSION_EXPIRED_ERRNOS:
            raise
        _connection.clear()
        cur = _cursor()
        cur.execute(sql)
    df = cur.fetch_pandas_all()
    df.columns = [c.lower() for c in df.columns]
    return df


def scatter_with_fit(df: pd.DataFrame, xcol: str, xtitle: str,
                     color_col: str | None = None) -> alt.Chart:
    """Scatter of `xcol` vs gross MWh with a pooled OLS fit line, tooltips, real titles.

    `color_col` (e.g. "season_year") colors the points to distinguish overlaid years;
    the fit line stays a single pooled regression regardless.
    """
    base = alt.Chart(df).encode(
        x=alt.X(f"{xcol}:Q", title=xtitle, scale=alt.Scale(zero=False)),
        y=alt.Y("total_gross_mwh:Q", title="daily gross generation (MWh)",
                scale=alt.Scale(zero=False)),
    )
    point_enc = {
        "tooltip": [
            alt.Tooltip("date:T", title="date"),
            alt.Tooltip(f"{xcol}:Q", title=xtitle, format=".1f"),
            alt.Tooltip("total_gross_mwh:Q", title="gross MWh", format=",.0f"),
        ]
    }
    if color_col:
        point_enc["color"] = alt.Color(f"{color_col}:N", title="year")
    points = base.mark_circle(size=60, opacity=0.45).encode(**point_enc)
    fit = base.transform_regression(xcol, "total_gross_mwh").mark_line(color="#e45756")
    return (points + fit).properties(height=380)


SEASON_MONTHS = {                 # (start_month, end_month); Winter wraps into year+1
    "Spring (Mar–May)": (3, 5),
    "Summer (Jun–Aug)": (6, 8),
    "Fall (Sep–Nov)": (9, 11),
    "Winter (Dec–Feb)": (12, 2),
    "Full year": (1, 12),
}

PRICE_SERIES = [
    "wti", "brent", "henryhub", "heatingoil", "propanemt", "jetfuel",
    "gasnyh", "gasgulf", "gasoline", "diesel", "coalppi", "natgasppi",
    "elecppi", "elecprice", "cpienergy",
]

MIN_R = 0.3  # below this, a straight-line weather baseline explains too little to be useful

# Generation-mix buckets: the 16 EIA-930 fuel codes grouped into recognizable sources,
# in stack order bottom → top (fossil base, renewables, storage discharge, other on top).
# The mart keeps every raw code (M-19); this grouping is presentation-only. Color is keyed
# on the bucket, so switching BA never repaints one — color follows the entity, not its
# rank. Hues CVD-checked (dataviz validator, worst adjacent ΔE 30) and reuse the app's
# tones (#4c78a8 = the primary blue). Storage = battery + pumped-storage *discharge* only
# (gross-positive, M-19) — a real feed to the grid. Any unlisted code folds into Other.
FUEL_MIX = [
    # (bucket label, hex, [member EIA-930 codes])
    ("Coal",        "#5c5c5c", ["COL"]),
    ("Nuclear",     "#7b6cd0", ["NUC"]),
    ("Natural gas", "#f58518", ["NG"]),
    ("Petroleum",   "#8c564b", ["OIL"]),
    ("Hydro",       "#4c78a8", ["WAT"]),
    ("Solar",       "#eeca3b", ["SUN", "SNB"]),            # SNB = solar + integrated battery
    ("Wind",        "#72b7b2", ["WND", "WNB"]),            # WNB = wind + integrated battery
    ("Storage",     "#e45756", ["BAT", "PS", "OES", "UES"]),  # discharge to grid
    ("Other",       "#bab0ac", ["OTH", "GEO", "UNK"]),
]


def season_range(season_key: str, year: int) -> tuple[str, str]:
    """(start, end) ISO dates for a season in a given year. Winter spans Dec Y → Feb Y+1."""
    sm, em = SEASON_MONTHS[season_key]
    if season_key == "Winter (Dec–Feb)":
        start = pd.Timestamp(year=year, month=12, day=1)
        end = pd.Timestamp(year=year + 1, month=2, day=1) + pd.offsets.MonthEnd(0)
    elif season_key == "Full year":
        start = pd.Timestamp(year=year, month=1, day=1)
        end = pd.Timestamp(year=year, month=12, day=31)
    else:
        start = pd.Timestamp(year=year, month=sm, day=1)
        end = pd.Timestamp(year=year, month=em, day=1) + pd.offsets.MonthEnd(0)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def add_season_coords(df: pd.DataFrame, season_key: str) -> pd.DataFrame:
    """Tag each row with `season_year` (color/label key) and `day_of_season` (overlay x).

    For Winter, Jan/Feb rows belong to the previous December's season, so `season_year`
    is year-1 there; `day_of_season` counts days from that season's start date.
    """
    month, year = df["date"].dt.month, df["date"].dt.year
    if season_key == "Winter (Dec–Feb)":
        season_year = year.where(month == 12, year - 1)
    else:
        season_year = year
    df = df.assign(season_year=season_year)
    starts = df["season_year"].map(
        lambda y: pd.Timestamp(season_range(season_key, int(y))[0])
    )
    return df.assign(day_of_season=(df["date"] - starts).dt.days)


def latest_complete_year(season_key: str, year_opts: list[int]) -> int | None:
    """Most recent year whose season has fully ended — so the default isn't a
    half-finished current season (a few noisy weeks). Falls back to the latest year."""
    today = pd.Timestamp.today().normalize()
    for y in year_opts:  # year_opts is sorted descending
        if pd.Timestamp(season_range(season_key, int(y))[1]) <= today:
            return y
    return year_opts[0] if year_opts else None


st.title("Zeus — energy platform")

# --- Global controls (sidebar) ----------------------------------------------
bas = query("select distinct ba from reporting.vw_energy_daily order by ba")["ba"].tolist()
with st.sidebar:
    st.header("Controls")
    ba = st.selectbox(
        "Balancing authority", bas,
        index=bas.index("PJM") if "PJM" in bas else 0,
        help="Drives the Generation, Weather, and Operators tabs. "
             "Prices are national (no BA).",
    )
    # `ba` is interpolated into SQL below; constrain it to the DB-provided allow-list
    # so a public visitor can't smuggle anything past the selectbox.
    if ba not in bas:
        st.error("Unknown balancing authority.")
        st.stop()
    today = pd.Timestamp.today().normalize()
    date_range = st.date_input(
        "Date range",
        value=((today - pd.Timedelta(days=730)).date(), today.date()),
        min_value=pd.Timestamp("2014-01-01").date(),
        max_value=today.date(),
        help="Drives the Generation, Prices, Operators, and Flows tabs. The Weather "
             "tab uses its own season + year picker; the flow map has its own day.",
    )
    # st.date_input returns a single date mid-selection; wait for both ends.
    if not (isinstance(date_range, (tuple, list)) and len(date_range) == 2):
        st.info("Pick a start and end date.")
        st.stop()
    start, end = (d.isoformat() for d in date_range)

# --- Data-health banner (always visible) ------------------------------------
health = query(
    "select "
    "(select max(date) from reporting.vw_energy_daily) as gen_latest, "
    "(select max(date) from reporting.vw_energy_daily where tmax is not null) as wx_latest, "
    "(select max(date) from reporting.vw_fuel_prices_daily) as px_latest, "
    "(select count(distinct ba) from reporting.vw_energy_daily "
    " where date = (select max(date) from reporting.vw_energy_daily)) as reported, "
    "(select count(distinct ba) from reporting.vw_energy_daily) as total_bas, "
    "(select count(distinct ba) from reporting.vw_energy_daily "
    " where date = (select max(date) from reporting.vw_energy_daily) "
    "   and hours_reported < 24) as partial, "
    "(select max(date) from reporting.vw_energy_daily where hours_reported >= 24) as gen_complete"
).iloc[0]
gen_latest = pd.to_datetime(health["gen_latest"]).date()
wx_latest = pd.to_datetime(health["wx_latest"]).date()
px_latest = pd.to_datetime(health["px_latest"]).date()
gen_complete = pd.to_datetime(health["gen_complete"]).date()
partial_flag = " (latest day partial)" if health["partial"] == health["reported"] else ""
st.caption(
    f"**Data health** — generation → {gen_latest}{partial_flag} · "
    f"weather → {wx_latest} (~3-day NOAA lag) · prices → {px_latest}  |  "
    f"{health['reported']}/{health['total_bas']} BAs reported on {gen_latest}. "
    "Detail in the **Data health** tab."
)

tab_gen, tab_wx, tab_px, tab_ops, tab_flows, tab_health = st.tabs(
    ["Generation", "Weather", "Prices", "Operators", "Flows", "Data health"]
)

# --- Generation tab ----------------------------------------------------------
with tab_gen:
    st.subheader(f"Renewable share — {ba}")
    st.caption(
        "Source: `REPORTING.VW_ENERGY_DAILY`. Share of gross generation from solar + "
        "wind + hydro, daily (ratio of sums, M-6) — the decarbonization trend and the "
        "seasonal rhythm of hydro, wind, and solar."
    )
    gen = query(
        f"select date, renewable_share "
        f"from reporting.vw_energy_daily where ba = '{ba}' "
        f"and date between '{start}' and '{end}' order by date"
    ).set_index("date")
    if gen.empty:
        st.info(f"No generation for {ba} in {start} → {end}.")
    else:
        st.line_chart(gen[["renewable_share"]])

    st.subheader("Generation mix by fuel — daily")
    st.caption(
        "Source: `REPORTING.VW_GENERATION_BY_FUEL_DAILY`. Daily gross generation (MWh) "
        f"for {ba}, stacked by fuel source (M-19). Gross = production only (storage "
        "charging clamped to 0), so the bands sum to the day's output — the fuel mix and "
        "how it shifts across seasons and years. The renewable share above is the "
        "solar + wind + hydro bands' share of this total."
    )
    mix = query(
        f"select date, fuel_type, gross_mwh "
        f"from reporting.vw_generation_by_fuel_daily where ba = '{ba}' "
        f"and date between '{start}' and '{end}' order by date"
    )
    if mix.empty:
        st.info(f"No generation-by-fuel for {ba} in {start} → {end}.")
    else:
        code_to_bucket = {code: label for label, _, codes in FUEL_MIX for code in codes}
        bucket_order = {label: i for i, (label, _, _) in enumerate(FUEL_MIX)}
        # Fold the 16 raw codes into display buckets, then sum so there's one row per
        # (date, bucket) — Solar = SUN + SNB, Storage = BAT + PS + …; unlisted → Other.
        mix["fuel"] = mix["fuel_type"].map(code_to_bucket).fillna("Other")
        agg = mix.groupby(["date", "fuel"], as_index=False)["gross_mwh"].sum()
        agg["fuel_order"] = agg["fuel"].map(bucket_order)
        agg["date"] = pd.to_datetime(agg["date"])
        domain = [label for label, _, _ in FUEL_MIX]
        rng = [color for _, color, _ in FUEL_MIX]
        mix_chart = (
            alt.Chart(agg)
            # white top-stroke on each band = a thin surface gap between stacked fuels
            .mark_area(line={"color": "white", "strokeWidth": 0.5})
            .encode(
                x=alt.X("date:T", title="date"),
                y=alt.Y("gross_mwh:Q", stack=True,
                        title="daily gross generation (MWh)"),
                color=alt.Color("fuel:N", title="fuel",
                                scale=alt.Scale(domain=domain, range=rng), sort=domain),
                order=alt.Order("fuel_order:Q"),  # deterministic stack order
                tooltip=[
                    alt.Tooltip("date:T", title="date"),
                    alt.Tooltip("fuel:N", title="fuel"),
                    alt.Tooltip("gross_mwh:Q", title="gross MWh", format=",.0f"),
                ],
            )
            .properties(height=380)
        )
        st.altair_chart(mix_chart, use_container_width=True)

    st.subheader("Intraday profile — the duck curve")
    st.caption(
        "Source: `REPORTING.VW_GENERATION_HOURLY`. Average generation by hour of day for "
        f"{ba} over the selected range. Renewables hump midday (solar); net generation "
        "dips overnight and ramps into the evening peak. Hours are **UTC** (M-7) — the "
        "solar peak lands at the BA's local solar-noon expressed in UTC."
    )
    duck = query(
        f"select hour(period) as hour_utc, "
        f"       avg(total_net_mwh) as net_mwh, "
        f"       avg(renewable_gross_mwh) as renewable_mwh "
        f"from reporting.vw_generation_hourly "
        f"where ba = '{ba}' and to_date(period) between '{start}' and '{end}' "
        f"group by hour(period) order by hour_utc"
    )
    if duck.empty:
        st.info(f"No hourly generation for {ba} in {start} → {end}.")
    else:
        duck_long = duck.melt(
            id_vars="hour_utc", value_vars=["net_mwh", "renewable_mwh"],
            var_name="series", value_name="mwh",
        ).replace({"net_mwh": "net generation", "renewable_mwh": "renewable generation"})
        duck_chart = (
            alt.Chart(duck_long)
            .mark_line(point=True)
            .encode(
                x=alt.X("hour_utc:Q", title="hour of day (UTC)",
                        axis=alt.Axis(values=list(range(0, 24, 2)))),
                y=alt.Y("mwh:Q", title="average MWh", scale=alt.Scale(zero=False)),
                color=alt.Color("series:N", title=None,
                                scale=alt.Scale(range=["#4c78a8", "#54a24b"])),
                tooltip=[
                    alt.Tooltip("hour_utc:Q", title="hour (UTC)"),
                    alt.Tooltip("series:N", title="series"),
                    alt.Tooltip("mwh:Q", title="avg MWh", format=",.0f"),
                ],
            )
            .properties(height=360)
        )
        st.altair_chart(duck_chart, use_container_width=True)

# --- Weather tab -------------------------------------------------------------
with tab_wx:
    st.subheader("Weather ⨝ generation — the D−1 lag")
    st.caption(
        "The mart joins weather **same-day** (M-7). Here we also shift weather back one "
        "day (`D−1`) to expose the stronger lagged signal: cooling load lags heat "
        "(thermal inertia). Pearson r for each join shown above each scatter. Weather "
        "lags ~3 days and partial days are excluded — see the **Data health** tab."
    )

    wx_bas = query(
        "select distinct ba from reporting.vw_energy_daily "
        "where tmax is not null order by ba"
    )["ba"].tolist()
    if ba not in wx_bas:
        st.info(
            f"**{ba} has no NOAA weather coverage** — the weather ⨝ generation "
            f"analysis isn't available for it. NOAA weather is ingested for "
            f"{len(wx_bas)} of the 71 balancing authorities: "
            f"{', '.join(wx_bas)}. {ba}'s generation is on the **Generation** tab."
        )
    else:
        cs, cy = st.columns([1, 2])
        with cs:
            season = st.selectbox(
                "Season", list(SEASON_MONTHS.keys()), index=1,  # default Summer
                help="A single season keeps the temp↔demand relationship monotonic; over a "
                     "full year it is U-shaped (heating + cooling both raise load).",
            )
        with cy:
            year_opts = query(
                f"select distinct year(date) as yr from reporting.vw_energy_daily "
                f"where ba = '{ba}' order by yr desc"
            )["yr"].tolist()
            _default_year = latest_complete_year(season, year_opts)
            years = st.multiselect(
                "Year(s)", year_opts,
                default=[_default_year] if _default_year is not None else [],
                help="One year → a single continuous curve. Add years to overlay and compare "
                     "them by day of season. Defaults to the latest *complete* season.",
            )

        if not years:
            st.info("Pick at least one year.")
        else:
            where_dates = " or ".join(
                f"(g.date between '{s}' and '{e}')"
                for s, e in (season_range(season, int(y)) for y in years)
            )
            lag = query(
                f"select g.date, g.total_gross_mwh, "
                f"       g.tmax as tmax_same_day, "
                f"       p.tmax as tmax_prior_day "
                f"from reporting.vw_energy_daily g "
                f"left join reporting.vw_energy_daily p "
                f"  on p.ba = g.ba and p.date = dateadd(day, -1, g.date) "
                f"where g.ba = '{ba}' "
                f"  and g.hours_reported >= 23 "  # exclude partial-reporting days (DST = 23h ok)
                f"  and ({where_dates}) "
                f"order by g.date"
            )
            lag["date"] = pd.to_datetime(lag["date"])  # temporal type for Altair tooltips
            lag = add_season_coords(lag, season)

            same_day = lag.dropna(subset=["tmax_same_day", "total_gross_mwh"])
            prior_day = lag.dropna(subset=["tmax_prior_day", "total_gross_mwh"])
            color_col = "season_year" if len(years) > 1 else None

            if len(same_day) > 2 and len(prior_day) > 2:
                r_same = same_day["tmax_same_day"].corr(same_day["total_gross_mwh"])
                r_prior = prior_day["tmax_prior_day"].corr(prior_day["total_gross_mwh"])
                c1, c2 = st.columns(2)
                with c1:
                    st.metric("Pearson r — same-day tmax → gross MWh", f"{r_same:.2f}")
                    st.altair_chart(
                        scatter_with_fit(same_day, "tmax_same_day", "same-day max temp (°C)",
                                         color_col),
                        use_container_width=True,
                    )
                    st.caption(f"tmax(D) vs gross MWh(D) — {len(same_day)} points")
                with c2:
                    st.metric(
                        "Pearson r — prior-day tmax(D−1) → gross MWh(D)",
                        f"{r_prior:.2f}",
                        delta=f"{r_prior - r_same:+.2f} vs same-day",
                    )
                    st.altair_chart(
                        scatter_with_fit(prior_day, "tmax_prior_day", "prior-day max temp (°C)",
                                         color_col),
                        use_container_width=True,
                    )
                    st.caption(f"tmax(D−1) vs gross MWh(D) — {len(prior_day)} points")
                if abs(r_prior) < MIN_R:
                    st.info(
                        f"A near-flat fit is expected here: **{season}** straddles the "
                        "heating→cooling crossover, so demand is **U-shaped** in temperature "
                        "— high at both the cold end (heating) and the warm end (cooling), "
                        "lowest in the mild middle — and a single line can't capture it. "
                        "**Summer** (cooling) and **Winter** (heating) are the monotonic "
                        "seasons where the lag signal shows."
                    )
            else:
                st.info(f"Not enough overlapping weather + generation days for "
                        f"{ba} / {season} / {years}.")

            st.subheader("Expected vs. actual — biggest deviations")
            st.caption(
                "Fit a least-squares line through the prior-day relationship above (the "
                "stronger 0.83 predictor), then ask: given that day's `tmax(D−1)`, how much "
                "generation did weather *predict*, and how far off was actual? Large gaps are "
                "days something other than temperature drove the grid — the rows worth "
                "investigating. Scenario baseline, not a forecast (no forward weather feed)."
            )
            if len(prior_day) <= 2:
                st.info(f"Not enough points to fit a baseline for {ba} / {season} / {years}.")
            elif abs(prior_day["tmax_prior_day"].corr(prior_day["total_gross_mwh"])) < MIN_R:
                _r = prior_day["tmax_prior_day"].corr(prior_day["total_gross_mwh"])
                st.warning(
                    f"Temperature explains too little of {ba}'s generation here "
                    f"(r = {_r:.2f}) for a straight-line baseline to mean anything. Full-year "
                    "demand is **U-shaped** in temperature — heating *and* cooling both raise "
                    "load — so one line can't fit it. Pick a single **season** above (not Full "
                    "year), where the relationship is monotonic, to get a valid baseline."
                )
            else:
                fit = prior_day.copy()
                slope, intercept = np.polyfit(fit["tmax_prior_day"], fit["total_gross_mwh"], 1)
                fit["expected"] = slope * fit["tmax_prior_day"] + intercept
                fit["residual"] = fit["total_gross_mwh"] - fit["expected"]
                fit["residual_pct"] = fit["residual"] / fit["expected"] * 100

                st.subheader("Actual vs. weather-implied baseline by day of season")
                # Plot on day-of-season so each year is one continuous curve, overlaid and
                # colored by year; no calendar gaps to interpolate across.
                baseline_long = fit.melt(
                    id_vars=["day_of_season", "season_year"],
                    value_vars=["total_gross_mwh", "expected"],
                    var_name="series", value_name="mwh",
                ).replace({"total_gross_mwh": "actual",
                           "expected": "expected (from tmax D−1)"})
                baseline_chart = (
                    alt.Chart(baseline_long)
                    .mark_line()
                    .encode(
                        x=alt.X("day_of_season:Q", title="day of season"),
                        y=alt.Y("mwh:Q", title="gross MWh", scale=alt.Scale(zero=False)),
                        color=alt.Color("season_year:N", title="year"),
                        strokeDash=alt.StrokeDash(
                            "series:N", title=None,
                            scale=alt.Scale(domain=["actual", "expected (from tmax D−1)"],
                                            range=[[1, 0], [4, 3]]),
                        ),
                        tooltip=[
                            alt.Tooltip("season_year:N", title="year"),
                            alt.Tooltip("day_of_season:Q", title="day of season"),
                            alt.Tooltip("series:N", title="series"),
                            alt.Tooltip("mwh:Q", title="MWh", format=",.0f"),
                        ],
                    )
                    .properties(height=360)
                )
                st.altair_chart(baseline_chart, use_container_width=True)
                st.caption("Solid = actual, dashed = expected (from tmax D−1); one color per year.")

                st.subheader("Days that deviated most from what weather predicted")
                top = fit.reindex(
                    fit["residual_pct"].abs().sort_values(ascending=False).index
                ).head(10)
                table = pd.DataFrame(
                    {
                        "date": top["date"].dt.date,
                        "year": top["season_year"],
                        "tmax(D−1)": top["tmax_prior_day"].round(1),
                        "actual MWh": top["total_gross_mwh"].round(0),
                        "expected MWh": top["expected"].round(0),
                        "Δ MWh": top["residual"].round(0),
                        "Δ %": top["residual_pct"].round(1),
                    }
                )
                st.dataframe(table, hide_index=True, use_container_width=True)
                st.caption(
                    f"{ba} / {season} / {years}. Positive Δ = ran *above* its temperature "
                    "baseline (grid worked harder than weather alone explains); negative = below."
                )

# --- Prices tab --------------------------------------------------------------
with tab_px:
    st.subheader("National energy prices — indexed to 100")
    st.caption(
        "Source: `REPORTING.VW_FUEL_PRICES_DAILY` (FRED, national). The raw series "
        "live in incompatible units ($/bbl, $/MMBtu, PPI index points), so each is "
        "**indexed to 100 at the start of the selected range** — one honest axis, and "
        "the interesting question becomes visible: *which* energy price actually "
        "moved, and by how much relative to the others?"
    )
    chosen = st.multiselect(
        "Series (up to 6)", PRICE_SERIES, default=["wti", "henryhub", "elecprice"],
        max_selections=6,
    )
    if not chosen:
        st.info("Pick at least one series.")
    else:
        cols = ", ".join(chosen)
        prices = query(
            f"select date, {cols} from reporting.vw_fuel_prices_daily "
            f"where date between '{start}' and '{end}' order by date"
        ).set_index("date")
        first_vals = prices.apply(
            lambda s: s.loc[s.first_valid_index()] if s.first_valid_index() else np.nan
        )
        indexed = (prices / first_vals * 100).reset_index()
        indexed["date"] = pd.to_datetime(indexed["date"])
        indexed_long = indexed.melt(
            id_vars="date", var_name="series", value_name="idx"
        ).dropna()
        # log y: a Henry Hub 12x spike would otherwise stretch a linear axis so
        # far that WTI doubling and elecprice's 11% drift both read as flat lines
        px_chart = (
            alt.Chart(indexed_long)
            .mark_line()
            .encode(
                x=alt.X("date:T", title="date"),
                y=alt.Y("idx:Q", title="index (100 = range start, log scale)",
                        scale=alt.Scale(type="log")),
                color=alt.Color("series:N", title=None),
                tooltip=[
                    alt.Tooltip("date:T", title="date"),
                    alt.Tooltip("series:N", title="series"),
                    alt.Tooltip("idx:Q", title="index", format=".0f"),
                ],
            )
            .properties(height=380)
        )
        st.altair_chart(px_chart, use_container_width=True)
        st.caption(
            "Log scale — equal vertical steps are equal *percentage* moves, so a "
            "doubling looks the same anywhere on the axis. 100 = the series' level "
            "at the range start. Spot prices (WTI, Henry Hub) swing hard; retail "
            "and PPI series are the slow-moving passthrough."
        )

# --- Operators tab -----------------------------------------------------------
with tab_ops:
    st.subheader("Forecast accuracy — scoring the operators")
    st.caption(
        "Source: `REPORTING.VW_DEMAND_ACCURACY` (the operators' own day-ahead demand "
        "forecast `DF` scored against actual demand `D`, per BA-day; M-15). **WAPE** = "
        "Σ|D−DF| / Σ|D| over the day's hours — lower is better. **Bias** is signed: "
        "positive = the operator systematically over-forecasts. Partial days "
        "(`hours_scored < 23`) are excluded, like everywhere else in this app."
    )

    range_days = (pd.Timestamp(end) - pd.Timestamp(start)).days or 1
    min_days = max(7, min(30, range_days // 2))
    league = query(
        f"select ba, avg(wape) as mean_wape, avg(bias_pct) as mean_bias, "
        f"       count(*) as days_scored "
        f"from reporting.vw_demand_accuracy "
        f"where forecaster = 'eia_df' and hours_scored >= 23 "
        f"and date between '{start}' and '{end}' "
        f"group by ba having count(*) >= {min_days} order by mean_wape"
    )
    if league.empty:
        st.info(
            f"No BA has at least {min_days} fully-scored days in {start} → {end}. "
            "Widen the date range."
        )
    else:
        st.subheader("League table — mean daily WAPE by BA")
        st.caption(
            f"{len(league)} demand-reporting BAs with ≥ {min_days} scored days in the "
            "selected range, best first. Generation-only BAs publish no demand or "
            "forecast, so they can't be scored."
        )
        league_chart = (
            alt.Chart(league)
            .mark_bar(color="#4c78a8", cornerRadiusEnd=4)
            .encode(
                x=alt.X("mean_wape:Q", title="mean daily WAPE",
                        axis=alt.Axis(format=".0%")),
                y=alt.Y("ba:N", title=None, sort="x"),
                tooltip=[
                    alt.Tooltip("ba:N", title="BA"),
                    alt.Tooltip("mean_wape:Q", title="mean WAPE", format=".2%"),
                    alt.Tooltip("mean_bias:Q", title="mean bias", format="+.2%"),
                    alt.Tooltip("days_scored:Q", title="days scored"),
                ],
            )
            .properties(height=max(240, 16 * len(league)))
        )
        st.altair_chart(league_chart, use_container_width=True)
        with st.expander("Full table"):
            st.dataframe(
                pd.DataFrame({
                    "BA": league["ba"],
                    "mean WAPE": (league["mean_wape"] * 100).round(2),
                    "mean bias %": (league["mean_bias"] * 100).round(2),
                    "days scored": league["days_scored"],
                }),
                hide_index=True, use_container_width=True,
            )

        st.subheader(f"Forecast error over time — {ba}")
        acc = query(
            f"select date, wape, bias_pct from reporting.vw_demand_accuracy "
            f"where ba = '{ba}' and forecaster = 'eia_df' and hours_scored >= 23 "
            f"and date between '{start}' and '{end}' order by date"
        )
        if acc.empty:
            st.info(
                f"**{ba} publishes no demand forecast** — generation-only BAs report "
                "NG/TI but no D/DF, so there is nothing to score. Pick a BA from the "
                "league table above."
            )
        else:
            m1, m2, m3 = st.columns(3)
            m1.metric("Mean daily WAPE", f"{acc['wape'].mean():.2%}")
            m2.metric("Mean bias", f"{acc['bias_pct'].mean():+.2%}",
                      help="Positive = over-forecast, negative = under-forecast (M-15).")
            m3.metric("Days scored", len(acc))

            acc["date"] = pd.to_datetime(acc["date"])
            acc_long = acc.melt(
                id_vars="date", value_vars=["wape", "bias_pct"],
                var_name="metric", value_name="value",
            ).replace({"wape": "WAPE", "bias_pct": "bias"})
            zero_rule = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
                color="#9a9a9a", strokeDash=[4, 3]
            ).encode(y="y:Q")
            err_chart = (
                alt.Chart(acc_long)
                .mark_line()
                .encode(
                    x=alt.X("date:T", title="date"),
                    y=alt.Y("value:Q", title="fraction of daily demand",
                            axis=alt.Axis(format="%")),
                    color=alt.Color("metric:N", title=None,
                                    scale=alt.Scale(domain=["WAPE", "bias"],
                                                    range=["#4c78a8", "#e45756"])),
                    tooltip=[
                        alt.Tooltip("date:T", title="date"),
                        alt.Tooltip("metric:N", title="metric"),
                        alt.Tooltip("value:Q", title="value", format="+.2%"),
                    ],
                )
                .properties(height=320)
            )
            st.altair_chart(zero_rule + err_chart, use_container_width=True)
            st.caption(
                "WAPE (blue) is always ≥ 0; bias (red) crossing the dashed zero line "
                "flips between over- and under-forecasting."
            )

            st.subheader(f"Does temperature break the forecast? — {ba}")
            st.caption(
                "Daily WAPE vs average temperature (`REPORTING.VW_ENERGY_DAILY`, M-3 "
                "tavg). Extreme heat and cold are the hard days — expect a U-shape, "
                "not a line, so no fit is drawn."
            )
            err_wx = query(
                f"select a.date, a.wape, e.tavg "
                f"from reporting.vw_demand_accuracy a "
                f"join reporting.vw_energy_daily e on e.ba = a.ba and e.date = a.date "
                f"where a.ba = '{ba}' and a.forecaster = 'eia_df' "
                f"and a.hours_scored >= 23 and e.tavg is not null "
                f"and a.date between '{start}' and '{end}'"
            )
            if err_wx.empty:
                st.info(
                    f"**{ba} has no NOAA weather coverage**, so the error↔temperature "
                    "view isn't available for it. The league table and error trend "
                    "above don't need weather."
                )
            else:
                err_wx["date"] = pd.to_datetime(err_wx["date"])
                wx_scatter = (
                    alt.Chart(err_wx)
                    .mark_circle(size=60, opacity=0.45, color="#4c78a8")
                    .encode(
                        x=alt.X("tavg:Q", title="daily average temperature (°C)",
                                scale=alt.Scale(zero=False)),
                        y=alt.Y("wape:Q", title="daily WAPE",
                                axis=alt.Axis(format="%")),
                        tooltip=[
                            alt.Tooltip("date:T", title="date"),
                            alt.Tooltip("tavg:Q", title="tavg (°C)", format=".1f"),
                            alt.Tooltip("wape:Q", title="WAPE", format=".2%"),
                        ],
                    )
                    .properties(height=340)
                )
                st.altair_chart(wx_scatter, use_container_width=True)

# --- Flows tab ----------------------------------------------------------------
# Polarity encoding shared by all three visuals (diverging, gray midpoint):
# blue = net exporter / exporting end, orange = net importer / importing end.
EXPORT_BLUE = "#4c78a8"
IMPORT_ORANGE = "#f58518"

with tab_flows:
    st.subheader("Interchange — who ships power to whom")
    st.caption(
        "Source: `REPORTING.VW_NET_POSITION_HOURLY` / `VW_INTERCHANGE_HOURLY` "
        "(BA-to-BA flows, each BA's own reports; M-22). Sign convention everywhere: "
        "**positive = net exporter, negative = net importer**. Neutral terms on "
        "purpose — importing isn't freeloading, it's usually buying cheaper or "
        "cleaner power than you can make."
    )

    st.subheader("Net importers vs net exporters")
    st.caption(
        f"Net interchange summed over {start} → {end}. Blue exports more than it "
        "imports; orange the reverse. Physical BAs only — EIA regional aggregates "
        "(US48, CAL, TEX, …) are excluded, they double-count their members (M-23)."
    )
    netpos = query(
        f"select ba, sum(net_position_mwh) as net_mwh "
        f"from reporting.vw_net_position_hourly "
        f"where to_date(period) between '{start}' and '{end}' "
        # the centroids seed doubles as the physical-BA registry (M-23)
        f"and ba in (select ba from reporting.vw_ba_centroids) "
        f"group by ba order by net_mwh desc"
    )
    if netpos.empty:
        st.info(f"No interchange data in {start} → {end}.")
    else:
        zero_x = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(
            color="#9a9a9a", strokeDash=[4, 3]
        ).encode(x="x:Q")
        net_bar = (
            alt.Chart(netpos)
            .mark_bar(cornerRadiusEnd=4)
            .encode(
                x=alt.X("net_mwh:Q", title="net interchange over range (MWh)"),
                y=alt.Y("ba:N", title=None, sort="-x"),
                color=alt.condition(
                    alt.datum.net_mwh >= 0,
                    alt.value(EXPORT_BLUE),
                    alt.value(IMPORT_ORANGE),
                ),
                tooltip=[
                    alt.Tooltip("ba:N", title="BA"),
                    alt.Tooltip("net_mwh:Q", title="net MWh", format="+,.0f"),
                ],
            )
            .properties(height=max(240, 14 * len(netpos)))
        )
        st.altair_chart(net_bar + zero_x, use_container_width=True)
        with st.expander("Full table"):
            st.dataframe(
                pd.DataFrame({
                    "BA": netpos["ba"],
                    "net MWh": netpos["net_mwh"].round(0),
                    "position": np.where(netpos["net_mwh"] >= 0,
                                         "net exporter", "net importer"),
                }),
                hide_index=True, use_container_width=True,
            )

    st.subheader("The daily rhythm — net position by hour of day")
    st.caption(
        "Average net position per hour (UTC, M-7) over the selected range, each BA "
        "**scaled to its own peak** so a small BA's rhythm isn't washed out by a "
        "giant's magnitudes — color shows the *shape* (blue hours export, orange "
        "hours import); the tooltip carries the real MWh. Solar BAs flip: midday "
        "export hump, evening import ramp."
    )
    rhythm = query(
        f"select ba, hour(period) as hour_utc, avg(net_position_mwh) as avg_net_mwh "
        f"from reporting.vw_net_position_hourly "
        f"where to_date(period) between '{start}' and '{end}' "
        f"and ba in (select ba from reporting.vw_ba_centroids) "
        f"group by ba, hour(period)"
    )
    if rhythm.empty:
        st.info(f"No interchange data in {start} → {end}.")
    else:
        peak = rhythm.groupby("ba")["avg_net_mwh"].transform(
            lambda s: s.abs().max()
        )
        rhythm["scaled"] = (rhythm["avg_net_mwh"] / peak.replace(0, np.nan)).fillna(0)
        # Rows ordered by overall net position: exporters top, importers bottom.
        ba_order = (
            rhythm.groupby("ba")["avg_net_mwh"].mean()
            .sort_values(ascending=False).index.tolist()
        )
        heat = (
            alt.Chart(rhythm)
            .mark_rect()
            .encode(
                x=alt.X("hour_utc:O", title="hour of day (UTC)"),
                y=alt.Y("ba:N", title=None, sort=ba_order),
                color=alt.Color(
                    "scaled:Q",
                    title="net position (share of own peak)",
                    # blue = export, gray ≈ 0, orange = import — same polarity
                    # pair as the bar above; reversed so positive lands on blue
                    scale=alt.Scale(scheme="blueorange", domain=[-1, 1], reverse=True),
                    legend=alt.Legend(format=".0%"),
                ),
                tooltip=[
                    alt.Tooltip("ba:N", title="BA"),
                    alt.Tooltip("hour_utc:O", title="hour (UTC)"),
                    alt.Tooltip("avg_net_mwh:Q", title="avg net MWh", format="+,.0f"),
                ],
            )
            .properties(height=max(300, 13 * rhythm["ba"].nunique()))
        )
        st.altair_chart(heat, use_container_width=True)

    st.subheader("Flow map — one day, hour by hour")
    st.caption(
        "Source: `REPORTING.VW_INTERCHANGE_HOURLY` ⨝ `VW_BA_CENTROIDS` (approximate "
        "footprint centers, M-23). Each arc is one reported flow, **blue end = "
        "exporter → orange end = importer**; width scales with MWh (shared scale "
        "across the day's hours, so scrubbing compares honestly). The day is "
        "fetched once; the slider filters client-side."
    )
    ic_latest = query(
        "select max(to_date(period)) as d from reporting.vw_interchange_hourly"
    ).iloc[0]["d"]
    if ic_latest is None:
        st.info("No interchange data yet.")
    else:
        ic_latest = pd.to_datetime(ic_latest).date()
        # Default to the latest *complete* day — the current day is mid-ingestion.
        default_day = (
            ic_latest - pd.Timedelta(days=1) if ic_latest >= today.date() else ic_latest
        )
        col_day, col_hour = st.columns([1, 2])
        with col_day:
            flow_day = st.date_input(
                "Day", value=default_day, max_value=ic_latest, key="flows_day",
            )
        with col_hour:
            flow_hour = st.slider("Hour (UTC)", 0, 23, 18, key="flows_hour")

        day_flows = query(
            f"select period, fromba, toba, flow_mwh "
            f"from reporting.vw_interchange_hourly "
            f"where to_date(period) = '{flow_day.isoformat()}' and flow_mwh > 0 "
            # physical BAs only (M-23): aggregate flows would double-draw their
            # members AND inflate the shared width scale below
            f"and fromba in (select ba from reporting.vw_ba_centroids) "
            f"and toba in (select ba from reporting.vw_ba_centroids)"
        )
        if day_flows.empty:
            st.info(f"No flows reported on {flow_day}.")
        else:
            cent = query(
                "select ba, ba_name, latitude, longitude from reporting.vw_ba_centroids"
            )
            day_flows["hour_utc"] = pd.to_datetime(day_flows["period"]).dt.hour
            day_max = day_flows["flow_mwh"].max()
            arcs = (
                day_flows[day_flows["hour_utc"] == flow_hour]
                .merge(cent.add_prefix("from_"), left_on="fromba", right_on="from_ba")
                .merge(cent.add_prefix("to_"), left_on="toba", right_on="to_ba")
            )
            unmapped = day_flows["hour_utc"].eq(flow_hour).sum() - len(arcs)
            if arcs.empty:
                st.info(f"No mappable flows at {flow_hour:02d}:00 UTC on {flow_day}.")
            else:
                arcs["flow_mwh"] = arcs["flow_mwh"].round(0).astype(int)
                arcs["width_px"] = 1 + 11 * arcs["flow_mwh"] / day_max

                m1, m2, m3 = st.columns(3)
                m1.metric("Flows this hour", len(arcs))
                m2.metric("Power moving", f"{arcs['flow_mwh'].sum():,.0f} MWh")
                _top = arcs.loc[arcs["flow_mwh"].idxmax()]
                m3.metric("Largest flow",
                          f"{_top['fromba']} → {_top['toba']}",
                          delta=f"{_top['flow_mwh']:,.0f} MWh", delta_color="off")

                arc_layer = pdk.Layer(
                    "ArcLayer",
                    data=arcs[["fromba", "toba", "flow_mwh", "width_px",
                               "from_longitude", "from_latitude",
                               "to_longitude", "to_latitude"]],
                    get_source_position=["from_longitude", "from_latitude"],
                    get_target_position=["to_longitude", "to_latitude"],
                    get_width="width_px",
                    get_source_color=[76, 120, 168, 200],    # EXPORT_BLUE
                    get_target_color=[245, 133, 24, 200],    # IMPORT_ORANGE
                    pickable=True,
                    auto_highlight=True,
                )
                point_layer = pdk.Layer(
                    "ScatterplotLayer",
                    data=cent,
                    get_position=["longitude", "latitude"],
                    get_radius=18000,
                    get_fill_color=[90, 90, 90, 160],
                    pickable=False,  # only arcs carry the tooltip
                )
                deck = pdk.Deck(
                    layers=[point_layer, arc_layer],
                    initial_view_state=pdk.ViewState(
                        latitude=39.5, longitude=-97.5, zoom=3.1, pitch=35,
                    ),
                    # Carto basemap — token-free, works on Community Cloud.
                    map_provider="carto",
                    map_style="light",
                    tooltip={"text": "{fromba} → {toba}: {flow_mwh} MWh"},
                )
                st.pydeck_chart(deck, use_container_width=True)
                note = (
                    f"{len(arcs)} flows at {flow_hour:02d}:00 UTC on {flow_day}. "
                    "Arcs run blue (exporter) → orange (importer); gray dots are "
                    "balancing authorities. A pair showing arcs both ways is the two "
                    "BAs disagreeing about the same flow — the asymmetry the dbt test "
                    "surfaces (M-22)."
                )
                if unmapped > 0:
                    note += (f" {unmapped} flow(s) hidden — BA code not in the "
                             "centroids seed yet (coverage drift, M-23).")
                st.caption(note)
                with st.expander("Flows table (this hour)"):
                    st.dataframe(
                        arcs[["fromba", "toba", "flow_mwh"]]
                        .sort_values("flow_mwh", ascending=False)
                        .rename(columns={"fromba": "from", "toba": "to",
                                         "flow_mwh": "MWh"}),
                        hide_index=True, use_container_width=True,
                    )

# --- Data health tab ---------------------------------------------------------
with tab_health:
    st.subheader("Data health")
    st.caption(
        "Freshness, coverage, and the exclusions the charts apply — so the numbers can "
        "be trusted, not guessed at."
    )

    fresh = pd.DataFrame(
        {
            "source": ["generation (EIA)", "weather (NOAA)", "prices (FRED)"],
            "latest date": [gen_latest, wx_latest, px_latest],
        }
    )
    st.table(fresh)
    st.caption(
        "Weather trails generation by ~3 days (NOAA publication lag, M-4) — expected, "
        "not missing data."
    )

    m1, m2, m3 = st.columns(3)
    m1.metric("BAs reporting (latest day)",
              f"{health['reported']}/{health['total_bas']}")
    m2.metric("Partial on latest day", int(health["partial"]),
              help="BAs with <24 hours on the latest date — the current day is still "
                   "ingesting.")
    m3.metric("Last fully-complete day", str(gen_complete))

    st.markdown(
        "- The **latest generation day is normally partial** — the daily run is "
        "mid-ingestion and the 7-day lookback backfills it. The last fully-complete day "
        "is shown above.\n"
        "- The **weather charts exclude `hours_reported < 23`** (partial days) and the "
        "trailing ~3-day weather-lag window, so a 4-hour current day can't skew a "
        "correlation or baseline."
    )
