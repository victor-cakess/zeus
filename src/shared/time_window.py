from datetime import date, datetime, timedelta, timezone


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def lookback_window(today: date, days: int) -> tuple[str, str]:
    """Return (start, end) in 'YYYY-MM-DDTHH' form, covering [today-days, today]."""
    start_dt = today - timedelta(days=days)
    return f"{start_dt.isoformat()}T00", f"{today.isoformat()}T23"


def lookback_window_dates(today: date, days: int) -> tuple[str, str]:
    """Return (start, end) in 'YYYY-MM-DD' form, covering [today-days, today]. For
    date-grain APIs (e.g. NOAA daily-summaries) that take plain dates, not hours."""
    return (today - timedelta(days=days)).isoformat(), today.isoformat()
