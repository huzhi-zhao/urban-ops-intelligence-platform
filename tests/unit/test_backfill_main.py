"""
Unit tests for the main entry: ``scripts.backfill.main``.

Covers:

- ``_discover_backfills()`` — auto-imports every ``backfill_*.py`` in the
  ``scripts.backfill`` package, populating ``BACKFILL_REGISTRY``.
- ``main()`` with an unknown ``--source`` → exit 1, stderr lists the
  available sources.
- ``main()`` with a known ``--source`` → dispatches to the registered
  handler, passing the remaining CLI args through (argv re-injection).
- ``main()`` does not call the handler if ``--source`` is missing
  (argparse's ``required=True`` handles this).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import scripts.backfill.main as main_mod  # noqa: E402
from scripts.backfill._registry import BACKFILL_REGISTRY  # noqa: E402


@pytest.fixture
def real_registry_snapshot():
    """Snapshot the registry, yield, then restore — without clearing first.

    Tests in this file depend on the *real* per-source scripts being
    registered (they don't add new entries). Clearing the registry would
    break ``_discover_backfills()`` because ``importlib.import_module``
    caches the modules and does not re-run the ``@register_backfill``
    decorator on a second call.

    We restore the snapshot on teardown in case a test mutated entries.

    Discovery runs *before* the snapshot is taken, and that ordering is
    load-bearing: registration is an import side effect, so a source whose
    module this test is the first to import would otherwise be registered
    inside the fixture, be absent from ``saved``, and get deleted on teardown.
    Later tests re-importing it would get a cached no-op module and see an
    empty registry entry — a failure that depends on test file ordering and
    points nowhere near the cause.
    """
    main_mod._discover_backfills()
    saved = dict(BACKFILL_REGISTRY)
    try:
        yield BACKFILL_REGISTRY
    finally:
        BACKFILL_REGISTRY.clear()
        BACKFILL_REGISTRY.update(saved)


# ── _discover_backfills ──────────────────────────────────────────────────────


def test_discover_backfills_populates_registry_with_real_sources(real_registry_snapshot):
    """Every per-source script is auto-discovered — dropping one in is enough.

    Compares against the scripts actually present rather than a fixed list of
    deployed sources: the property under test is "discovery finds all of them",
    not "these particular cities exist".
    """
    import pkgutil

    import scripts.backfill as pkg

    expected = {
        m.name.removeprefix("backfill_")
        for m in pkgutil.iter_modules(pkg.__path__)
        if m.name.startswith("backfill_")
    }
    assert expected, "no per-source backfill scripts found at all"

    main_mod._discover_backfills()
    assert len(BACKFILL_REGISTRY) == len(expected)


def test_discover_backfills_ignores_non_backfill_modules(real_registry_snapshot):
    """A module like ``_common`` (no ``backfill_`` prefix) is not imported."""
    import pkgutil

    import scripts.backfill as pkg
    names = [info.name for info in pkgutil.iter_modules(pkg.__path__)]
    # Sanity: there are modules in this package that should NOT be loaded.
    assert any(not n.startswith("backfill_") for n in names)
    main_mod._discover_backfills()
    # The registry contains only backfill_* handlers.
    for source_id in BACKFILL_REGISTRY:
        assert source_id.startswith("SRC-")  # all our real handlers do


# ── main(): error paths ──────────────────────────────────────────────────────


def test_main_unknown_source_returns_1_and_lists_available(real_registry_snapshot, capsys):
    main_mod._discover_backfills()
    rc = main_mod.main(["--source", "SRC-FAKE-999"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "SRC-FAKE-999" in captured.err
    # Lists the real sources so the operator knows the options.
    assert "Available:" in captured.err
    for source_id in BACKFILL_REGISTRY:
        assert source_id in captured.err


def test_main_missing_source_flag_returns_2_argparse_exit(real_registry_snapshot, capsys):
    """argparse exits with code 2 when ``--source`` is missing — we propagate."""
    main_mod._discover_backfills()
    with pytest.raises(SystemExit) as exc_info:
        main_mod.main([])
    assert exc_info.value.code == 2


def test_main_does_not_call_handler_when_source_unknown(real_registry_snapshot):
    """A handler must not be invoked when the source is unknown."""
    handler = MagicMock()
    BACKFILL_REGISTRY["SRC-PLACEHOLDER"] = handler

    main_mod.main(["--source", "SRC-NOT-IN-REGISTRY"])
    handler.assert_not_called()


# ── main(): dispatch + argv re-injection ─────────────────────────────────────


def test_main_dispatches_to_correct_handler(real_registry_snapshot):
    """The registered handler is invoked with a parsed args Namespace."""
    # Replace the handler with a mock so we can inspect the call.
    fake_handler = MagicMock()
    BACKFILL_REGISTRY["SRC-TEST-DAILY"] = fake_handler

    with patch("scripts.backfill.main.parse_args") as mock_parse_args:
        mock_parse_args.return_value = argparse.Namespace(
            start=date(2026, 6, 1),
            end=date(2026, 6, 8),
            action="upload",
            bucket="bkt",
            dataset=None,
            dry_run=False,
        )
        rc = main_mod.main([
            "--source", "SRC-TEST-DAILY",
            "--start", "2026-06-01",
            "--end", "2026-06-08",
            "--bucket", "bkt",
        ])

    assert rc == 0
    fake_handler.assert_called_once()
    args = fake_handler.call_args.args[0]
    assert args.start == date(2026, 6, 1)
    assert args.end == date(2026, 6, 8)
    assert args.bucket == "bkt"
    assert args.action == "upload"


def test_main_reinjects_remaining_argv_into_parse_args(real_registry_snapshot):
    """After parsing ``--source``, the rest of argv is forwarded so the
    per-source script's ``parse_args`` sees them."""
    fake_handler = MagicMock()
    BACKFILL_REGISTRY["SRC-TEST-DAILY"] = fake_handler

    with patch("scripts.backfill.main.parse_args") as mock_parse_args:
        mock_parse_args.return_value = argparse.Namespace(
            start=date(2026, 6, 1), end=date(2026, 6, 8),
            action="upload", bucket="bkt", dataset=None, dry_run=False,
        )
        main_mod.main([
            "--source", "SRC-TEST-DAILY",
            "--start", "2026-06-01",
            "--end", "2026-06-08",
            "--bucket", "bkt",
            "--dataset", "service_requests",
        ])

    # parse_args should have been called with the description derived from
    # the handler's docstring and with sys.argv containing the remaining args.
    assert mock_parse_args.called
    # sys.argv should be the re-injected version, not the original.
    assert sys.argv[0] in sys.argv
    # The first non-program arg in sys.argv should be --start, not --source.
    non_prog = [a for a in sys.argv if not a.startswith("--source")]
    assert "--start" in non_prog
    assert "--source" not in sys.argv[1:]


def test_main_returns_zero_on_successful_dispatch(real_registry_snapshot):
    fake_handler = MagicMock()
    BACKFILL_REGISTRY["SRC-TEST-DAILY"] = fake_handler

    with patch("scripts.backfill.main.parse_args") as mock_parse_args:
        mock_parse_args.return_value = argparse.Namespace(
            start=date(2026, 6, 1), end=date(2026, 6, 8),
            action="fetch", bucket=None, dataset=None, dry_run=False,
        )
        rc = main_mod.main(["--source", "SRC-TEST-DAILY"])

    assert rc == 0


# ── main() uses handler's docstring as the parse_args description ────────────


def test_main_passes_handler_docstring_as_description(real_registry_snapshot):
    """The description forwarded to the inner ``parse_args`` is the
    registered handler's ``__doc__`` (so ``--help`` shows the right text)."""
    fake_handler = MagicMock()
    fake_handler.__doc__ = "MY-CUSTOM-HANDLER-DESCRIPTION"
    BACKFILL_REGISTRY["SRC-TEST-CUSTOM"] = fake_handler

    with patch("scripts.backfill.main.parse_args") as mock_parse_args:
        mock_parse_args.return_value = argparse.Namespace()
        main_mod.main(["--source", "SRC-TEST-CUSTOM"])

    description = mock_parse_args.call_args.args[0]
    assert description == "MY-CUSTOM-HANDLER-DESCRIPTION"


def test_main_falls_back_to_default_description_when_handler_has_no_docstring(real_registry_snapshot):
    fake_handler = MagicMock()
    fake_handler.__doc__ = None
    BACKFILL_REGISTRY["SRC-NO-DOC"] = fake_handler

    with patch("scripts.backfill.main.parse_args") as mock_parse_args:
        mock_parse_args.return_value = argparse.Namespace()
        main_mod.main(["--source", "SRC-NO-DOC"])

    assert mock_parse_args.call_args.args[0] == "backfill"


# ── Parametrized dispatch: each real source is routable ──────────────────────


def test_main_can_dispatch_to_each_real_source(real_registry_snapshot):
    """Smoke: every discovered source can be reached via main().

    Iterates the registry instead of a hardcoded id list, so a newly added
    source is covered the moment its script lands.
    """
    main_mod._discover_backfills()
    source_ids = sorted(BACKFILL_REGISTRY)
    assert source_ids, "no per-source backfill scripts found at all"

    for source_id in source_ids:
        # Replace the handler with a mock so the call is observable without
        # running real backfill logic.
        fake_handler = MagicMock()
        BACKFILL_REGISTRY[source_id] = fake_handler

        with patch("scripts.backfill.main.parse_args") as mock_parse_args:
            mock_parse_args.return_value = argparse.Namespace(
                start=date(2026, 6, 1), end=date(2026, 6, 8),
                action="upload", bucket="bkt", dataset=None, dry_run=False,
            )
            rc = main_mod.main(["--source", source_id])

        assert rc == 0, f"main() failed to dispatch {source_id}"
        fake_handler.assert_called_once()
