from datetime import date, datetime, timedelta, timezone


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def lookback_window(today: date, days: int) -> tuple[str, str]:
    """Return (start, end) in 'YYYY-MM-DDTHH' form, covering [today-days, today]."""
    start_dt = today - timedelta(days=days)
    return f"{start_dt.isoformat()}T00", f"{today.isoformat()}T23"
