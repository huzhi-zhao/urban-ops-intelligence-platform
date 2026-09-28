"""Contract regression for the service-request source against its live Socrata API.

Replaces the retired-instance test that queried another city's portal. The
point is unchanged: an upstream field rename or removal is an escalate-to-human
event (CLAUDE.md), and a routine `make test-unit` should surface it.

Everything the checks depend on is read, not written here:

- which portal and resource to query — the source registry
  (``config/sources/*.yaml``), the only place the instance lives;
- which fields must be present and which values they may take — the frozen
  Bronze contract (``contracts/api-contracts/*.yaml``), fields declared
  ``nullable: false`` and ``domain: [...]``.

So the test carries no city literal (guardrail §1), and tightening the contract
tightens the test without editing it.

The live check is marked ``network`` so offline runs can exclude it
(``-m "not network"``, which ``make test-unit-offline`` does). The offline
checks below it keep the registry and the contract from drifting apart, which
is what would make the live check test the wrong thing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from ingestion.config.loader import load_source_config

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = REPO_ROOT / "contracts" / "api-contracts"

# The one piece of routing this test needs: which registered source is the
# service-request stream. A source id is config data, quoted verbatim.
SOURCE_ID = "SRC-WPG-311"

# Socrata omits a key entirely rather than sending null, so a field that is
# legitimately sometimes absent must be `nullable: true` in the contract, or
# this test becomes a coin flip on which rows were sampled.
SAMPLE_SIZE = 200

# A Socrata *floating* timestamp: local wall-clock, no offset, no 'Z'. If the
# upstream ever starts sending an offset, every Silver timestamp conversion
# would double-shift — that is exactly the kind of change to escalate.
FLOATING_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$")


def _contract() -> dict[str, Any]:
    matches = []
    for path in sorted(CONTRACT_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and doc.get("source_id") == SOURCE_ID:
            matches.append(doc)
    assert len(matches) == 1, f"expected one contract for {SOURCE_ID}, found {len(matches)}"
    return matches[0]


def _required_fields(contract: dict[str, Any]) -> list[str]:
    return [f["name"] for f in contract["fields"] if f.get("nullable") is False]


def _domains(contract: dict[str, Any]) -> dict[str, set[str]]:
    return {f["name"]: set(f["domain"]) for f in contract["fields"] if "domain" in f}


# ── Offline: the registry and the contract describe the same dataset ─────────


def test_the_contract_and_the_registry_name_the_same_dataset() -> None:
    contract = _contract()
    source = load_source_config(SOURCE_ID)
    names = [ds.name for ds in source.datasets]
    assert contract["dataset"] in names
    assert contract["partition_strategy"] == source.strategy_for(
        next(ds for ds in source.datasets if ds.name == contract["dataset"])
    )


def test_the_contract_and_the_registry_agree_on_the_key_and_the_timestamp() -> None:
    contract = _contract()
    dataset = next(
        ds for ds in load_source_config(SOURCE_ID).datasets if ds.name == contract["dataset"]
    )
    # The registry's primary_key drives dag_audit_bronze check B; the
    # contract's is the semantic authority. The source YAML says so and asks
    # that both change together — this is where that is enforced.
    assert list(dataset.primary_key) == list(contract["primary_key"])
    required = _required_fields(contract)
    assert dataset.timestamp_field in required
    assert set(contract["primary_key"]) <= set(required)


def test_the_live_check_has_something_to_check() -> None:
    contract = _contract()
    assert len(_required_fields(contract)) >= 3
    assert _domains(contract), "no field declares a value domain"


# ── Live: the upstream still has the shape the contract froze ────────────────


@pytest.mark.network
def test_live_records_match_the_frozen_contract() -> None:
    from ingestion.clients.socrata_client import SocrataClient

    contract = _contract()
    dataset = next(
        ds for ds in load_source_config(SOURCE_ID).datasets if ds.name == contract["dataset"]
    )
    client = SocrataClient(resource_id=dataset.resource_id, domain=dataset.domain)
    # Newest rows: schema drift shows up in what is being published now.
    records = client.fetch_page(
        limit=SAMPLE_SIZE, extra_params={"$order": f"{dataset.timestamp_field} DESC"}
    )
    assert records, "the live API returned no rows"

    # Reported in aggregate, so a rename reads as "absent on 200/200" rather
    # than as one confusing row.
    for field in _required_fields(contract):
        missing = sum(1 for r in records if field not in r)
        assert missing == 0, (
            f"'{field}' is missing on {missing}/{len(records)} rows. The contract declares it "
            f"non-nullable; if it is absent on every row the upstream schema changed — "
            f"escalate per CLAUDE.md instead of relaxing this test."
        )

    for field, allowed in _domains(contract).items():
        seen = {r[field] for r in records if field in r}
        assert seen <= allowed, f"'{field}' has values outside the contract: {seen - allowed}"

    stamps = [r[dataset.timestamp_field] for r in records]
    bad = [s for s in stamps if not FLOATING_TIMESTAMP.match(s)]
    assert not bad, f"'{dataset.timestamp_field}' is no longer a floating timestamp: {bad[:3]}"

    for record in records:
        geometry = record.get("geometry")
        if geometry is None:
            continue
        assert geometry.get("type") == "Point", geometry
        lon, lat = geometry["coordinates"]
        float(lon), float(lat)
