"""Shared test fixtures + helpers.

pytest auto-discovers this file and makes anything defined here (fixtures) available
to every test under tests/ without an import. `pythonpath = ["src"]` in pyproject
lets tests do `from shared import ...` exactly as the Lambdas do at runtime.
"""

import importlib.util
import os
from datetime import date

import pytest

# src/shared/{ssm,s3_io,sns,snowflake_io}.py build their boto3 client at import time
# (module-level singletons). Client construction needs a region, and CI runners have
# none configured (→ botocore NoRegionError). The tests never make a real AWS call —
# every client is faked — so a dummy region just satisfies construction. setdefault so
# a real local region is never clobbered. Must run BEFORE importing shared.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from shared import ingest, s3_io, snowflake_io, ssm, time_window  # noqa: E402


def load_module(path: str, name: str):
    """Import a .py file by its filesystem path, under an explicit module name.

    Each source's schema.py / client.py share the SAME module name across the three
    ingest dirs (they sit at the zip root at runtime, imported flat as `import schema`).
    So `import schema` is ambiguous in one test process. This loads each file directly
    and registers it under a unique name we pick, sidestepping the collision.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module_loader():
    """Expose load_module to tests as a fixture (conftest isn't importable, so a plain
    `from tests.conftest import load_module` won't work — fixtures are the channel)."""
    return load_module


# --- Fixtures for the run_ingest orchestrator tests -------------------------------


FIXED_TODAY = date(2026, 7, 5)


@pytest.fixture
def ingest_config():
    """A hand-built IngestConfig — bypasses config_from_env() (which reads os.environ).
    The Snowflake values are dummies; no real connection is ever made (copy_into is
    stubbed by the fake_snowflake fixture)."""
    return ingest.IngestConfig(
        source="eia",
        bucket="test-bucket",
        lookback_days=7,
        max_workers=2,
        snowflake=ingest.SnowflakeConfig(
            account="acct",
            user="user",
            role="role",
            warehouse="wh",
            database="ZEUS_DEV",
            schema="EIA",
            table="EIA_GRID",
            stage="EIA_STAGE",
            key_ssm_path="/zeus/dev/snowflake/eia_loader_private_key",
        ),
    )


class FakeS3:
    """In-memory stand-in for src/shared/s3_io — a dict keyed by S3 key.

    This is a *test double* (a fake), not a mock: it has real behaviour (put then read
    back), so run_ingest's consolidation step reads exactly what its fetch step wrote —
    the same round-trip the real S3 does, without the network. We record puts so tests
    can assert what landed (e.g. the run report, the curated Parquet).
    """

    def __init__(self):
        self.store: dict[str, object] = {}

    def put_json(self, bucket, key, obj):
        self.store[key] = obj

    def put_bytes(self, bucket, key, body):
        self.store[key] = body

    def list_keys(self, bucket, prefix):
        return [k for k in self.store if k.startswith(prefix)]

    def iter_objects(self, bucket, prefix):
        for k in self.list_keys(bucket, prefix):
            yield self.store[k]


@pytest.fixture
def fake_s3(monkeypatch):
    """Swap the real S3 functions for the in-memory fake.

    We monkeypatch the attributes ON the s3_io module object. Because shared/ingest.py
    did `from shared import ... s3_io`, it holds a reference to the SAME module object —
    so replacing s3_io.put_json here replaces the function ingest.py will call. This is
    the convention CLAUDE.md documents ("tests substitute them by monkeypatching module
    attributes"). monkeypatch auto-undoes every setattr at test teardown.
    """
    fake = FakeS3()
    monkeypatch.setattr(s3_io, "put_json", fake.put_json)
    monkeypatch.setattr(s3_io, "put_bytes", fake.put_bytes)
    monkeypatch.setattr(s3_io, "list_keys", fake.list_keys)
    monkeypatch.setattr(s3_io, "iter_objects", fake.iter_objects)
    return fake


@pytest.fixture(autouse=True)
def fake_externals(monkeypatch):
    """Neutralise the remaining external calls for every ingest test (autouse=True means
    it applies without being requested by name):
      - ssm.get_parameter → a dummy PEM string (never used; copy_into is stubbed)
      - time_window.today_utc → a fixed date, so key paths and report dates are stable
    copy_into is left to the fake_snowflake fixture, which the happy/error tests tune.
    """
    monkeypatch.setattr(ssm, "get_parameter", lambda path: "dummy-pem")
    monkeypatch.setattr(time_window, "today_utc", lambda: FIXED_TODAY)


@pytest.fixture
def fake_snowflake(monkeypatch):
    """Stub snowflake_io.copy_into to return a fixed row count instead of connecting.
    Returns a small controller so a test can flip it to raise (the load-failure case)."""

    class Controller:
        rows_loaded = 42
        error: Exception | None = None

        def copy_into(self, **kwargs):
            if self.error:
                raise self.error
            return self.rows_loaded

    controller = Controller()
    monkeypatch.setattr(snowflake_io, "copy_into", controller.copy_into)
    return controller
