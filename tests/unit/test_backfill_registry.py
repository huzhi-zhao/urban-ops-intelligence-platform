"""
Unit tests for the @register_backfill decorator and BACKFILL_REGISTRY.

The registry is module-level mutable state shared with the per-source scripts
(``backfill_*.py``), so every test in this file uses the :func:`fresh_registry`
fixture to clear and restore the dict. This avoids cross-test pollution and
also keeps the real per-source registrations intact across the test session.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.backfill._registry import BACKFILL_REGISTRY, register_backfill  # noqa: E402


@pytest.fixture
def fresh_registry():
    """Snapshot BACKFILL_REGISTRY, clear it for the test, then restore."""
    saved = dict(BACKFILL_REGISTRY)
    BACKFILL_REGISTRY.clear()
    try:
        yield BACKFILL_REGISTRY
    finally:
        BACKFILL_REGISTRY.clear()
        BACKFILL_REGISTRY.update(saved)


# ── Happy path ───────────────────────────────────────────────────────────────


def test_register_backfill_adds_to_registry(fresh_registry):
    @register_backfill("SRC-TEST-001")
    def run(args: argparse.Namespace) -> None:
        return None

    assert "SRC-TEST-001" in BACKFILL_REGISTRY
    assert BACKFILL_REGISTRY["SRC-TEST-001"] is run


def test_decorator_returns_function_unchanged(fresh_registry):
    @register_backfill("SRC-TEST-002")
    def run(args: argparse.Namespace) -> None:
        """A docstring that should survive decoration."""
        return None

    # The wrapper must return the original function, not a wrapper.
    assert run.__name__ == "run"
    assert run.__doc__ == "A docstring that should survive decoration."
    assert callable(run)


def test_multiple_sources_are_independent(fresh_registry):
    @register_backfill("SRC-A")
    def run_a(args: argparse.Namespace) -> None:
        return "a"

    @register_backfill("SRC-B")
    def run_b(args: argparse.Namespace) -> None:
        return "b"

    assert BACKFILL_REGISTRY["SRC-A"] is run_a
    assert BACKFILL_REGISTRY["SRC-B"] is run_b
    assert len(BACKFILL_REGISTRY) == 2
    # Smoke-check that each registered handler is callable with an args namespace.
    ns = argparse.Namespace()
    assert run_a(ns) == "a"
    assert run_b(ns) == "b"


def test_every_per_source_script_registers_its_source_id():
    """Importing a ``backfill_*.py`` must register its ``SOURCE_ID``.

    Asserts the invariant rather than a list of deployed sources. The old
    version enumerated the cities that happened to be deployed, so retiring one
    turned this red for no reason, and — worse — adding one did not turn it red
    at all: a new script whose decorator was forgotten would silently no-op
    under ``_discover_backfills``, which is exactly the failure this guards.
    """
    import importlib
    import pkgutil

    import scripts.backfill as pkg

    script_names = [
        m.name for m in pkgutil.iter_modules(pkg.__path__)
        if m.name.startswith("backfill_")
    ]
    assert script_names, "no per-source backfill scripts found at all"

    for name in script_names:
        module = importlib.import_module(f"scripts.backfill.{name}")
        assert hasattr(module, "SOURCE_ID"), f"{name} defines no SOURCE_ID"
        assert module.SOURCE_ID in BACKFILL_REGISTRY, (
            f"{name} defines SOURCE_ID={module.SOURCE_ID!r} but did not register "
            f"it — is the @register_backfill decorator missing?"
        )


def test_every_registered_source_id_exists_in_the_config_registry():
    """A registered handler with no source YAML would fail only at run time."""
    import importlib
    import pkgutil

    import scripts.backfill as pkg
    from ingestion.config import load_all_sources

    for m in pkgutil.iter_modules(pkg.__path__):
        if m.name.startswith("backfill_"):
            importlib.import_module(f"scripts.backfill.{m.name}")

    known = set(load_all_sources())
    for source_id in BACKFILL_REGISTRY:
        if source_id.startswith("SRC-TEST-") or source_id.startswith("SRC-DUP"):
            continue  # ids injected by other tests in this module
        assert source_id in known, (
            f"{source_id} has a backfill script but no config/sources/*.yaml"
        )


# ── Error branches ───────────────────────────────────────────────────────────


def test_duplicate_source_id_raises_runtime_error(fresh_registry):
    @register_backfill("SRC-DUP")
    def first(args: argparse.Namespace) -> None:
        return None

    with pytest.raises(RuntimeError) as exc_info:
        @register_backfill("SRC-DUP")
        def second(args: argparse.Namespace) -> None:
            return None

    msg = str(exc_info.value)
    assert "SRC-DUP" in msg
    assert "already registered" in msg
    # The second function should NOT have replaced the first.
    assert BACKFILL_REGISTRY["SRC-DUP"] is first


def test_registration_after_duplicate_does_not_leak(fresh_registry):
    """A failed re-registration must not leave the new function in the dict."""
    @register_backfill("SRC-LEAK")
    def first(args: argparse.Namespace) -> None:
        return None

    second_caught = False
    try:
        @register_backfill("SRC-LEAK")
        def second(args: argparse.Namespace) -> None:
            return None
    except RuntimeError:
        second_caught = True

    assert second_caught
    assert "second" not in BACKFILL_REGISTRY  # second is local; only first is registered
    assert BACKFILL_REGISTRY["SRC-LEAK"] is first


def test_registry_starts_empty_under_fresh_fixture(fresh_registry):
    """The fixture gives a clean slate."""
    assert BACKFILL_REGISTRY == {}


def test_source_id_with_special_characters_is_allowed(fresh_registry):
    """The registry itself does not validate source_id format — that's the
    YAML loader's job. The decorator just uses the string as a dict key."""
    @register_backfill("SRC-Some-Custom_Id.123")
    def run(args: argparse.Namespace) -> None:
        return None

    assert "SRC-Some-Custom_Id.123" in BACKFILL_REGISTRY
