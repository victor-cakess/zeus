from datetime import date


def raw_prefix(source: str, d: date) -> str:
    return (
        f"raw/{source}/"
        f"ingestion_year={d.year:04d}/"
        f"ingestion_month={d.month:02d}/"
        f"ingestion_day={d.day:02d}/"
    )


def curated_prefix(source: str, d: date) -> str:
    return (
        f"curated/{source}/"
        f"ingestion_year={d.year:04d}/"
        f"ingestion_month={d.month:02d}/"
        f"ingestion_day={d.day:02d}/"
    )


def raw_key(source: str, unit: str, d: date) -> str:
    return raw_prefix(source, d) + f"{unit}.json"
