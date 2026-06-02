from collections import Counter
from datetime import date


def _reason(error) -> str:
    """Return a readable skip reason from a per-BA error (a plain string)."""
    return str(error) if error else "unknown"


def build_run_report(d: date, rows: int, results: list[dict]) -> dict:
    succeeded = [r["unit"] for r in results if r.get("status") == "ok"]
    skipped = [
        {"unit": r["unit"], "reason": _reason(r.get("error"))}
        for r in results
        if r.get("status") == "skipped"
    ]
    return {
        "date": d.isoformat(),
        "rows": rows,
        "succeeded": succeeded,
        "skipped": skipped,
    }


def skip_history(reports: list[dict]) -> dict[str, int]:
    """Count skips per unit across the given run reports."""
    counts: Counter[str] = Counter()
    for report in reports:
        for entry in report.get("skipped", []):
            counts[entry["unit"]] += 1
    return dict(counts)


def format_email(
    run_report: dict, history: dict[str, int], source: str, history_days: int
) -> tuple[str, str]:
    n_ok = len(run_report["succeeded"])
    n_skip = len(run_report["skipped"])
    label = source.upper()

    subject = f"{label} run {run_report['date']} — {n_ok} ok / {n_skip} skipped"

    lines = [
        f"{label} ingestion complete — {run_report['date']}",
        f"Succeeded: {n_ok} | Skipped: {n_skip}",
    ]

    sf = run_report.get("snowflake")
    if sf and sf["status"] == "ok":
        lines.append(f"Snowflake: loaded {sf['rows_loaded']} rows")
    elif sf:
        lines.append(f"Snowflake: FAILED — {sf['error']}")

    if run_report["skipped"]:
        lines.append("")
        lines.append("Skipped this run:")
        for entry in run_report["skipped"]:
            lines.append(f"  {entry['unit']} — {entry['reason']}")

    if history:
        lines.append("")
        lines.append(f"Skip history (last {history_days} days):")
        for unit, count in sorted(history.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {unit} — {count} days")

    return subject, "\n".join(lines)
