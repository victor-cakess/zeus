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


def build_dbt_report(d: date, res) -> dict:
    """Shape a dbtRunner result into the dbt run report that format_dbt_section
    renders. res is duck-typed (success/exception/result.results) so this module
    never imports dbt; res.result can be None (e.g. parse/connection failure
    before anything ran)."""
    results = getattr(res.result, "results", None) or []
    models_built = sum(
        1
        for r in results
        if str(r.node.resource_type) == "model" and str(r.status) == "success"
    )
    tests = [r for r in results if str(r.node.resource_type) == "test"]
    failed_tests = [r.node.name for r in tests if str(r.status) in ("fail", "error")]
    return {
        "date": d.isoformat(),
        "status": "ok" if res.success else "failed",
        "models_built": models_built,
        "tests_passed": sum(1 for r in tests if str(r.status) == "pass"),
        "tests_failed": len(failed_tests),
        "failed_tests": failed_tests,
        "error": str(res.exception) if res.exception else None,
    }


def format_dbt_section(dbt_report: dict | None) -> str:
    """Body block for the daily dbt run. dbt isn't a fan-out source (no
    succeeded/skipped units), so it gets its own section, not a source section.
    A None report means dbt crashed before writing one (see failure alert)."""
    if dbt_report is None:
        return "DBT: no run report (it crashed before writing one — see failure alert)."

    lines = [
        f"DBT build — {dbt_report['date']}",
        f"Models built: {dbt_report['models_built']}"
        f" | Tests passed: {dbt_report['tests_passed']}"
        f" | Tests failed: {dbt_report['tests_failed']}",
    ]
    if dbt_report["failed_tests"]:
        lines.append("")
        lines.append("Failed tests:")
        for name in dbt_report["failed_tests"]:
            lines.append(f"  {name}")
    if dbt_report.get("error"):
        lines.append("")
        lines.append(f"Error: {dbt_report['error']}")

    return "\n".join(lines)


def format_digest(
    date_str: str, sections: list[tuple], history_days: int, dbt_report: dict | None = None
) -> tuple[str, str]:
    """Combine one day's per-source run into a single email.

    `sections` is a list of (source, run_report | None, history) — one per source.
    A None run_report means that pipeline wrote no report today (it crashed and its
    own on-failure alert already fired); it's surfaced here, not hidden.
    `dbt_report` is the dbt run's report, rendered as its own section.
    """
    bodies = []
    for source, run_report, history in sections:
        label = source.upper()
        if run_report is None:
            bodies.append(f"{label}: no run report for {date_str} (see failure alert).")
            continue
        _, body = format_email(run_report, history, source, history_days)
        bodies.append(body)

    bodies.append(format_dbt_section(dbt_report))

    # Static subject: SNS caps subjects at 100 ASCII chars, so per-source detail
    # lives in the body, not the title.
    subject = f"Zeus daily report — {date_str}"
    return subject, "\n\n".join(bodies)
