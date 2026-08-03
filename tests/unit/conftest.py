"""Shared fixtures for the unit suite."""

from __future__ import annotations

from pathlib import Path

import pytest

SYNTHETIC_SOURCES_DIR = Path(__file__).parent.parent / "fixtures" / "sources"

# Role-named synthetic source ids, one per partition strategy. Tests that
# exercise generic machinery should reference these rather than whichever
# cities happen to be deployed — see tests/fixtures/sources/README.md.
TEST_DAILY = "SRC-TEST-DAILY"
TEST_WIDE = "SRC-TEST-WIDE"
TEST_MONTHLY = "SRC-TEST-MONTHLY"
TEST_STATIC = "SRC-TEST-STATIC"
TEST_SNAPSHOT = "SRC-TEST-SNAPSHOT"


@pytest.fixture
def synthetic_sources(monkeypatch):
    """Point the config loader at the synthetic role-named source registry.

    The loader re-reads the directory on every call and holds no cache, so
    setting the env var is enough — nothing needs invalidating.

    Returns the fixture directory, for tests that want to add a file to it.
    """
    monkeypatch.setenv("UOIP_CONFIG_DIR", str(SYNTHETIC_SOURCES_DIR))
    return SYNTHETIC_SOURCES_DIR
