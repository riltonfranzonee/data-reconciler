from __future__ import annotations

from pathlib import Path

import pytest

from ror_reconcile.ingest import load_ror_records
from ror_reconcile.matcher import RorMatcher
from ror_reconcile.store import RorStore

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def fixture_store(tmp_path: Path):
    store = RorStore(tmp_path / "ror.sqlite")
    store.replace_records(load_ror_records(FIXTURE_DIR / "ror_fixture.json"))
    try:
        yield store
    finally:
        store.close()


@pytest.fixture()
def matcher(fixture_store: RorStore):
    return RorMatcher(fixture_store)
