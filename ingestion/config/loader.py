"""
YAML source registry loader.

Resolves the ``config/sources/`` directory (with optional ``UOIP_CONFIG_DIR``
override), parses each YAML file, and validates it through the Pydantic
models in :mod:`ingestion.config.source_config`. All errors are wrapped in
:class:`ConfigLoadError` with the offending file path included in the message.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from ingestion.config.source_config import SourceConfig

_DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "sources"
CONFIG_DIR_ENV_VAR = "UOIP_CONFIG_DIR"


class ConfigLoadError(ValueError):
    """Raised when a source config file cannot be loaded or validated."""


def _config_dir() -> Path:
    """Return the config directory, honoring ``UOIP_CONFIG_DIR`` if set."""
    override = os.environ.get(CONFIG_DIR_ENV_VAR)
    return Path(override) if override else _DEFAULT_CONFIG_DIR


def _read_yaml(path: Path) -> dict:
    """Parse a YAML file into a dict, wrapping errors with the file path."""
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigLoadError(f"Corrupt YAML in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigLoadError(
            f"Top-level YAML in {path} must be a mapping, got {type(raw).__name__}",
        )
    return raw


def _validate(raw: dict, *, path: Path) -> SourceConfig:
    """Validate a parsed YAML dict against the SourceConfig schema."""
    try:
        return SourceConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigLoadError(f"Validation failed for {path}: {e}") from e


def _index_source_files() -> dict[str, Path]:
    """Scan the config dir and build a ``source_id -> file path`` map.

    The source id is read from each YAML's ``source.id`` field — we don't rely
    on filename-to-id conventions, which keeps new sources zero-config.
    """
    index: dict[str, Path] = {}
    for path in sorted(_config_dir().glob("*.yaml")):
        try:
            raw = _read_yaml(path)
        except ConfigLoadError:
            # If the YAML is broken, defer the error to load_all_sources so
            # a single bad file is surfaced with full context.
            index[path.stem] = path
            continue
        sid = raw.get("source", {}).get("id") if isinstance(raw.get("source"), dict) else None
        if not isinstance(sid, str):
            # No recognizable source id — let the full validator surface this.
            index[path.stem] = path
            continue
        if sid in index:
            raise ConfigLoadError(
                f"Duplicate source id {sid!r} in {path.name} "
                f"(also defined in {index[sid].name})",
            )
        index[sid] = path
    return index


def load_source_config(source_id: str) -> SourceConfig:
    """Load and validate a single source's YAML config by source id."""
    index = _index_source_files()
    path = index.get(source_id)
    if path is None:
        available = ", ".join(sorted(index)) or "(none)"
        raise ConfigLoadError(
            f"Source config not found for {source_id!r}. "
            f"Looked in {_config_dir()}. Available: {available}",
        )
    raw = _read_yaml(path)
    return _validate(raw, path=path)


def load_all_sources() -> dict[str, SourceConfig]:
    """Load and validate every YAML file in the config dir.

    Returns a mapping ``{source_id: SourceConfig}``. The first validation
    failure is raised as :class:`ConfigLoadError` with the offending file path.
    """
    out: dict[str, SourceConfig] = {}
    for path in sorted(_config_dir().glob("*.yaml")):
        raw = _read_yaml(path)
        cfg = _validate(raw, path=path)
        if cfg.source.id in out:
            raise ConfigLoadError(
                f"Duplicate source id {cfg.source.id!r} in {path.name}",
            )
        out[cfg.source.id] = cfg
    return out


def load_datasets_by_strategy(strategy: str) -> list[tuple[str, str]]:
    """Every ``(source_id, dataset_name)`` whose effective strategy is ``strategy``.

    Lives here rather than in the DAG that consumes it, for two reasons: DAG
    files are supposed to hold scheduling logic only, and — more practically —
    apache-airflow is not installed in the unit environment, so anything defined
    inside a DAG module is untestable until it reaches a container.

    Uses each dataset's *effective* strategy, so a per-dataset override is
    honoured: a weather source whose archive is ``daily`` and whose forecast is
    ``snapshot`` appears under both.
    """
    return [
        (cfg.source.id, ds.name)
        for cfg in load_all_sources().values()
        for ds in cfg.datasets_with_strategy(strategy)
    ]
