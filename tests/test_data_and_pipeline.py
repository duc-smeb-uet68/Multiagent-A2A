from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import FrozenInstanceError
from decimal import Decimal
import json
from pathlib import Path
import socket
from typing import Any
import zipfile

import pytest

from multiagent_a2a import RunConfig, run_pipeline
from multiagent_a2a.application.validation import validate_existing_outputs
from multiagent_a2a.artifacts.qa import ArtifactQaError
from multiagent_a2a.data import OlistRepository, load_cases
from multiagent_a2a.llm.qwen import QwenGateway


EXPECTED_ISSUES = {
    "canceled_order_paid": 8,
    "unavailable_order_paid": 8,
    "late_delivery_seller": 8,
    "late_delivery_logistics": 8,
    "valid_split_payment": 9,
    "unsupported_late_claim": 9,
}
EXPECTED_STATUSES = {"action_required": 32, "no_action": 18}
EXPECTED_TOTALS = {
    "item_total_brl": Decimal("4686.52"),
    "freight_total_brl": Decimal("727.47"),
    "payment_total_brl": Decimal("7782.89"),
    "recommended_refund_brl": Decimal("3429.64"),
}
EXPECTED_SOURCE_ROWS = {
    "orders": 99_441,
    "items": 112_650,
    "payments": 103_886,
    "sellers": 3_095,
}
EXPECTED_RETAINED_ROWS = {
    "orders": 50,
    "items": 48,
    "payments": 60,
    "sellers": 40,
}
EXPECTED_TRACE_EVENTS = {
    "backend_initialized": 1,
    "case_started": 50,
    "dispatch": 150,
    "handoff": 150,
    "decision": 50,
    "verification": 50,
    "case_completed": 50,
    "run_completed": 1,
}
CASE_FIXTURES = {
    "EC_005": {
        "issue": "unavailable_order_paid",
        "status": "action_required",
        "cause": "ORDER_UNAVAILABLE_AFTER_PAYMENT",
        "action": "issue_full_refund",
        "entity_lengths": {"order_ids": 1, "item_ids": 0, "seller_ids": 0, "payment_ids": 1},
        "totals": ("0.0", "0.0", "1191.5", "1191.5"),
        "parties": [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}],
    },
    "EC_025": {
        "issue": "valid_split_payment",
        "status": "no_action",
        "cause": "MULTIPLE_PAYMENTS_RECONCILED",
        "action": "explain_valid_split_payment",
        "entity_lengths": {"order_ids": 1, "item_ids": 3, "seller_ids": 1, "payment_ids": 2},
        "totals": ("133.05", "51.27", "184.32", "0.0"),
        "parties": [],
        "seller_id": "391fc6631aebcf3004804e51b40bcf1e",
    },
    "EC_029": {
        "issue": "late_delivery_seller",
        "status": "action_required",
        "cause": "SELLER_HANDOFF_AFTER_LIMIT",
        "action": "refund_freight",
        "entity_lengths": {"order_ids": 1, "item_ids": 3, "seller_ids": 1, "payment_ids": 1},
        "totals": ("449.7", "42.51", "492.21", "42.51"),
        "parties": [
            {"party_type": "seller", "party_id": "88460e8ebdecbfecb5f9601833981930"}
        ],
        "seller_id": "88460e8ebdecbfecb5f9601833981930",
    },
    "EC_030": {
        "issue": "valid_split_payment",
        "status": "no_action",
        "cause": "MULTIPLE_PAYMENTS_RECONCILED",
        "action": "explain_valid_split_payment",
        "entity_lengths": {"order_ids": 1, "item_ids": 1, "seller_ids": 1, "payment_ids": 3},
        "totals": ("15.9", "9.94", "25.84", "0.0"),
        "parties": [],
        "seller_id": "cfb1a033743668a192316f3c6d1d2671",
    },
}


def _money_tuple(payload: dict[str, Any]) -> tuple[str, str, str, str]:
    financial = payload["financial_resolution"]
    return tuple(
        str(financial[key])
        for key in (
            "item_total_brl",
            "freight_total_brl",
            "payment_total_brl",
            "recommended_refund_brl",
        )
    )


def _assert_case_fixture(payload: dict[str, Any], expected: dict[str, Any]) -> None:
    assert payload["assessment"] == {
        "primary_issue": expected["issue"],
        "case_status": expected["status"],
        "confidence": 0.99,
    }
    entities = payload["affected_entities"]
    assert {key: len(value) for key, value in entities.items()} == expected[
        "entity_lengths"
    ]
    if "seller_id" in expected:
        assert entities["seller_ids"] == [expected["seller_id"]]
    assert payload["root_cause_analysis"] == {
        "ranked_causes": [{"cause_code": expected["cause"], "rank": 1}],
        "responsible_parties": expected["parties"],
    }
    assert payload["resolution_actions"] == [expected["action"]]
    assert payload["financial_resolution"]["currency"] == "BRL"
    assert _money_tuple(payload) == expected["totals"]


def test_official_loaders_and_edge_case_rows(
    official_cases, cases_by_id, official_repository
) -> None:
    assert len(official_cases) == 50
    assert [case.case_id for case in official_cases] == [
        f"EC_{index:03d}" for index in range(1, 51)
    ]
    assert len({case.claimed_order_id for case in official_cases}) == 50
    assert official_repository.source_row_counts == EXPECTED_SOURCE_ROWS
    assert official_repository.retained_row_counts == EXPECTED_RETAINED_ROWS

    expected_cardinality = {
        "EC_005": (0, 1),
        "EC_025": (3, 2),
        "EC_029": (3, 1),
        "EC_030": (1, 3),
    }
    for case_id, (item_count, payment_count) in expected_cardinality.items():
        order_id = cases_by_id[case_id].claimed_order_id
        assert len(official_repository.get_items(order_id)) == item_count
        assert len(official_repository.get_payments(order_id)) == payment_count

    case_025 = cases_by_id["EC_025"]
    item_rows = official_repository.get_items(case_025.claimed_order_id)
    assert [row["order_item_id"] for row in item_rows] == ["1", "2", "3"]
    assert all(official_repository.is_known_seller(row["seller_id"]) for row in item_rows)

    order = official_repository.get_order(case_025.claimed_order_id)
    original_status = order["order_status"]
    order["order_status"] = "tampered"
    assert (
        official_repository.get_order(case_025.claimed_order_id)["order_status"]
        == original_status
    )
    with pytest.raises(FrozenInstanceError):
        official_cases[0].case_id = "changed"


@pytest.mark.integration
def test_official_deterministic_pipeline_artifacts(
    tmp_path: Path,
    data_dir: Path,
    input_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Deterministic test attempted model discovery or network access")

    monkeypatch.setattr(QwenGateway, "_discover_source", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    config = RunConfig(
        data_dir=data_dir,
        input_dir=input_dir,
        work_root=tmp_path,
        enable_llm=False,
        mirror_logging=True,
        strict_official_assertions=True,
        expected_case_count=50,
    )
    progress: list[tuple[int, int]] = []
    report = run_pipeline(
        config,
        progress_callback=lambda done, total: progress.append((done, total)),
    )

    assert progress == [(10, 50), (20, 50), (30, 50), (40, 50), (50, 50)]
    assert report.cases_processed == 50
    assert report.qwen_validated_cases == 0
    assert report.deterministic_fallback_cases == 50
    assert report.verifier_repairs == 0
    assert report.qa.output_count == 50
    assert report.qa.issue_counts == EXPECTED_ISSUES
    assert report.qa.status_counts == EXPECTED_STATUSES
    assert report.qa.aggregate_totals == EXPECTED_TOTALS

    expected_names = {f"EC_{index:03d}.json" for index in range(1, 51)}
    output_paths = sorted(config.output_dir.iterdir())
    assert {path.name for path in output_paths} == expected_names
    disk_payloads = {
        path.stem: json.loads(path.read_text(encoding="utf-8")) for path in output_paths
    }
    for case_id, expected in CASE_FIXTURES.items():
        _assert_case_fixture(disk_payloads[case_id], expected)

    with zipfile.ZipFile(config.submission_zip) as archive:
        assert len(archive.namelist()) == 50
        assert set(archive.namelist()) == expected_names
        assert all("/" not in name and "\\" not in name for name in archive.namelist())
        for name in archive.namelist():
            archived = json.loads(archive.read(name).decode("utf-8"))
            assert archived == disk_payloads[Path(name).stem]

    assert config.trace_path.read_bytes() == config.mirrored_trace_path.read_bytes()
    trace_lines = config.trace_path.read_text(encoding="utf-8").splitlines()
    assert len(trace_lines) == 502
    events = [json.loads(line) for line in trace_lines]
    assert Counter(event["event"] for event in events) == Counter(EXPECTED_TRACE_EVENTS)
    run_ids = {event["run_id"] for event in events}
    assert len(run_ids) == 1
    per_case_events: defaultdict[str, int] = defaultdict(int)
    for event in events:
        if event["case_id"] is not None:
            per_case_events[event["case_id"]] += 1
    assert len(per_case_events) == 50
    assert set(per_case_events.values()) == {10}

    assert config.metadata_path.read_bytes() == config.mirrored_metadata_path.read_bytes()
    metadata = json.loads(config.metadata_path.read_text(encoding="utf-8"))
    assert metadata["model"]["configured_model"] == "Qwen/Qwen3-8B"
    assert metadata["model"]["requested"] is False
    assert metadata["model"]["ready"] is False
    assert metadata["model"]["status"] == "disabled_deterministic_fallback"
    assert metadata["model"]["network_permitted"] is False
    assert metadata["model"]["calls"] == 0
    assert metadata["policy_version"] == "EC_POLICY_V1"
    assert metadata["data"]["source_row_counts"] == EXPECTED_SOURCE_ROWS
    assert metadata["data"]["retained_row_counts"] == EXPECTED_RETAINED_ROWS
    assert metadata["run"]["run_id"] == next(iter(run_ids))
    assert metadata["run"]["cases_processed"] == 50
    assert metadata["run"]["qwen_validated_cases"] == 0
    assert metadata["run"]["deterministic_fallback_cases"] == 50
    assert metadata["run"]["verifier_repairs"] == 0
    assert metadata["run"]["trace_events"] == 502
    assert metadata["run"]["qa"]["issue_counts"] == EXPECTED_ISSUES
    assert metadata["run"]["qa"]["status_counts"] == EXPECTED_STATUSES
    assert metadata["run"]["qa"]["aggregate_totals"] == {
        key: str(value) for key, value in EXPECTED_TOTALS.items()
    }

    tampered_path = config.output_dir / "EC_001.json"
    tampered = json.loads(tampered_path.read_text(encoding="utf-8"))
    tampered["evidence_ids"][0] = "order:THIS_DOES_NOT_EXIST"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ArtifactQaError, match="Disk/in-memory mismatch"):
        validate_existing_outputs(
            data_dir=data_dir,
            input_dir=input_dir,
            output_dir=config.output_dir,
            zip_path=tmp_path / "validated.zip",
            expected_case_count=50,
            strict_official_assertions=True,
        )


def test_case_loader_rejects_file_set_and_schema(
    case_dir_factory, valid_case_payload
) -> None:
    valid_directory = case_dir_factory([valid_case_payload])
    assert load_cases(valid_directory, 1)[0].claimed_order_id == "order-1"

    (valid_directory / "extra.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Input set mismatch"):
        load_cases(valid_directory, 1)
    (valid_directory / "extra.json").unlink()

    malformed = dict(valid_case_payload)
    malformed["unexpected"] = True
    (valid_directory / "EC_001.json").write_text(
        json.dumps(malformed), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Invalid schema"):
        load_cases(valid_directory, 1)


def test_case_loader_rejects_policy_and_duplicate_order(
    case_dir_factory, valid_case_payload
) -> None:
    wrong_policy = json.loads(json.dumps(valid_case_payload))
    wrong_policy["policy_version"] = "EC_POLICY_V2"
    directory = case_dir_factory([wrong_policy])
    with pytest.raises(ValueError, match="Unsupported policy"):
        load_cases(directory, 1)

    first = json.loads(json.dumps(valid_case_payload))
    second = json.loads(json.dumps(valid_case_payload))
    second["case_id"] = "EC_002"
    directory = case_dir_factory([first, second])
    with pytest.raises(ValueError, match="Duplicate claimed_order_id"):
        load_cases(directory, 2)


def test_olist_loader_validates_keys_foreign_keys_and_missing_orders(
    olist_dir_factory,
) -> None:
    valid_directory = olist_dir_factory()
    repository = OlistRepository.load_for_orders(valid_directory, ["order-1"])
    assert repository.retained_row_counts == {
        "orders": 1,
        "items": 1,
        "payments": 1,
        "sellers": 1,
    }
    assert repository.is_known_seller("seller-1")
    with pytest.raises(KeyError, match="absent from orders"):
        OlistRepository.load_for_orders(valid_directory, ["missing-order"])

    unknown_seller_directory = olist_dir_factory(
        item_seller="seller-missing", seller_rows=[{"seller_id": "seller-1"}]
    )
    with pytest.raises(ValueError, match="absent from sellers"):
        OlistRepository.load_for_orders(unknown_seller_directory, ["order-1"])

    duplicate_directory = olist_dir_factory(duplicate_item=True)
    with pytest.raises(ValueError, match="must be unique"):
        OlistRepository.load_for_orders(duplicate_directory, ["order-1"])
