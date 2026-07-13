"""Put the repo's src/ and the EIA interchange-data Lambda dir on sys.path so the
backfill scripts can reuse the production code unchanged:

    from shared import paths, s3_io   # src/shared/
    import client                     # src/lambdas/eia_interchange/ingest/client.py
    import schema                     # src/lambdas/eia_interchange/ingest/schema.py

Import this module (for its side effect) before importing any of the above.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_PATHS = [
    _REPO_ROOT / "src",
    _REPO_ROOT / "src" / "lambdas" / "eia_interchange" / "ingest",
]

for _p in _PATHS:
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)
