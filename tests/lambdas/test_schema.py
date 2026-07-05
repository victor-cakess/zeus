"""Tests for each source's schema.py::normalize_row (EIA / FRED / NOAA).

normalize_row turns one raw API dict into the flat row that gets written to Parquet.
Two things every test asserts:
  1. The field mapping is correct (raw API names → standard names, type coercion,
     None-handling for missing values).
  2. The output row is ACCEPTED by that source's declared pa.schema, via
     pa.Table.from_pylist([row], schema=SCHEMA). This catches drift between
     normalize_row and SCHEMA — if normalize starts emitting a key/type the schema
     doesn't declare, the table build raises, and the test fails.

Import note: the three schema.py files share the module name `schema` (they sit at the
zip root at runtime). We can't `import schema` unambiguously, so we load each by file
path with the module_loader fixture (wrapping conftest.load_module).
"""

from datetime import date, datetime
from pathlib import Path

import pyarrow as pa
import pytest

# Repo root: tests/lambdas/test_schema.py → parents[2] is the project root.
ROOT = Path(__file__).resolve().parents[2]


def _load(module_loader, source):
    path = ROOT / "src" / "lambdas" / source / "ingest" / "schema.py"
    # Unique name per source so the three loads don't collide in sys.modules.
    return module_loader(str(path), f"{source}_schema")


def _assert_schema_accepts(row, schema):
    # Proves the normalized row is loadable under the declared Parquet schema.
    table = pa.Table.from_pylist([row], schema=schema)
    assert table.num_rows == 1


# --- EIA ---------------------------------------------------------------------------


def test_eia_normalize_maps_dashed_keys(module_loader):
    mod = _load(module_loader, "eia")
    raw = {
        "period": "2026-07-05T14",
        "respondent": "PJM",
        "respondent-name": "PJM Interconnection",
        "fueltype": "NG",
        "type-name": "Natural Gas",
        "value": "1234.5",
        "value-units": "megawatthours",
    }
    row = mod.normalize_row(raw, date(2026, 7, 5))

    assert row["period"] == datetime(2026, 7, 5, 14)   # 'T14' parsed to a datetime
    assert row["respondent_name"] == "PJM Interconnection"  # dash → underscore
    assert row["type_name"] == "Natural Gas"
    assert row["value"] == 1234.5                       # string → float
    assert row["ingestion_date"] == date(2026, 7, 5)
    _assert_schema_accepts(row, mod.SCHEMA)


def test_eia_normalize_missing_value_is_none(module_loader):
    mod = _load(module_loader, "eia")
    raw = {"period": "2026-07-05T14"}  # no 'value' key
    row = mod.normalize_row(raw, date(2026, 7, 5))
    assert row["value"] is None
    _assert_schema_accepts(row, mod.SCHEMA)


# --- FRED --------------------------------------------------------------------------


def test_fred_normalize(module_loader):
    mod = _load(module_loader, "fred")
    raw = {"series": "WTI", "series_id": "DCOILWTICO", "date": "2026-07-05", "value": "80.25"}
    row = mod.normalize_row(raw, date(2026, 7, 5))

    assert row["series"] == "WTI"
    assert row["date"] == date(2026, 7, 5)   # string → date
    assert row["value"] == 80.25             # string → float
    _assert_schema_accepts(row, mod.SCHEMA)


# --- NOAA (wide, 13 datatypes) -----------------------------------------------------


def test_noaa_normalize_wide_row(module_loader):
    mod = _load(module_loader, "noaa")
    raw = {
        "DATE": "2026-07-05",
        "STATION": "USW00013739",
        "ba": "PJM",
        "TMAX": "31.1",
        "PRCP": "0.0",
        # TMIN and the rest omitted → NCEI didn't report them → should land None.
    }
    row = mod.normalize_row(raw, date(2026, 7, 5))

    assert row["date"] == date(2026, 7, 5)
    assert row["station"] == "USW00013739"
    assert row["ba"] == "PJM"
    assert row["tmax"] == 31.1          # upper-case datatype → lower-case field, float
    assert row["prcp"] == 0.0
    assert row["tmin"] is None          # omitted datatype → None
    _assert_schema_accepts(row, mod.SCHEMA)


@pytest.mark.parametrize("empty", [None, ""])
def test_noaa_to_float_treats_blank_as_none(module_loader, empty):
    # NCEI can send "" for a reported-but-blank value; _to_float maps both None and "" → None.
    mod = _load(module_loader, "noaa")
    raw = {"DATE": "2026-07-05", "STATION": "S", "ba": "PJM", "TMAX": empty}
    row = mod.normalize_row(raw, date(2026, 7, 5))
    assert row["tmax"] is None
