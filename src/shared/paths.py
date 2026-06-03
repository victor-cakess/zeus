from datetime import date


def _partition(d: date) -> str:
    return (
        f"ingestion_year={d.year:04d}/"
        f"ingestion_month={d.month:02d}/"
        f"ingestion_day={d.day:02d}/"
    )


def _layer_prefix(layer: str, source: str, d: date) -> str:
    return f"{layer}/{source}/{_partition(d)}"


def raw_prefix(source: str, d: date) -> str:
    return _layer_prefix("raw", source, d)


def curated_prefix(source: str, d: date) -> str:
    return _layer_prefix("curated", source, d)


def report_prefix(source: str, d: date) -> str:
    return _layer_prefix("reports", source, d)


def raw_key(source: str, unit: str, d: date) -> str:
    return raw_prefix(source, d) + f"{unit}.json"


def report_key(source: str, d: date) -> str:
    return report_prefix(source, d) + "run_report.json"


def partition_date(key: str) -> date:
    """Inverse of the ingestion_year=/month=/day= partition this module builds —
    parse the ingestion date back out of any key under a partitioned prefix."""
    p = dict(s.split("=") for s in key.split("/") if "=" in s)
    return date(int(p["ingestion_year"]), int(p["ingestion_month"]), int(p["ingestion_day"]))
