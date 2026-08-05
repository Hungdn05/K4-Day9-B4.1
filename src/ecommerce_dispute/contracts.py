"""Input/output contract checks shared by every later pipeline stage."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import POLICY_VERSION


class CaseContractError(ValueError):
    """Raised when an input or output JSON does not meet the lab contract."""


@dataclass(frozen=True)
class CaseRequest:
    case_id: str
    language: str
    message: str
    claimed_order_id: str
    include_customer_history: bool
    include_product_context: bool
    policy_version: str


def read_case_request(path: Path) -> CaseRequest:
    """Parse and validate one input case before any agent receives it."""

    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaseContractError(f"Cannot parse input case {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CaseContractError(f"{path.name}: top-level JSON must be an object")

    required_top_level = {"case_id", "customer_request", "investigation_scope", "policy_version"}
    if set(raw) != required_top_level:
        raise CaseContractError(f"{path.name}: expected fields {sorted(required_top_level)}")
    request = raw["customer_request"]
    scope = raw["investigation_scope"]
    if not isinstance(request, dict) or not isinstance(scope, dict):
        raise CaseContractError(f"{path.name}: request and scope must be objects")
    if set(request) != {"language", "message", "claimed_order_id"}:
        raise CaseContractError(f"{path.name}: invalid customer_request fields")
    if set(scope) != {"include_customer_history", "include_product_context"}:
        raise CaseContractError(f"{path.name}: invalid investigation_scope fields")
    values = (raw["case_id"], request["language"], request["message"], request["claimed_order_id"])
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise CaseContractError(f"{path.name}: IDs and request text must be non-empty strings")
    if raw["policy_version"] != POLICY_VERSION:
        raise CaseContractError(f"{path.name}: expected policy_version {POLICY_VERSION}")
    if not all(isinstance(scope[key], bool) for key in scope):
        raise CaseContractError(f"{path.name}: scope values must be booleans")
    return CaseRequest(
        case_id=raw["case_id"], language=request["language"], message=request["message"],
        claimed_order_id=request["claimed_order_id"],
        include_customer_history=scope["include_customer_history"],
        include_product_context=scope["include_product_context"], policy_version=raw["policy_version"],
    )


def required_case_paths(input_dir: Path) -> tuple[Path, ...]:
    """Return exactly the 50 required case paths, rejecting missing or extra JSON."""

    expected = tuple(input_dir / f"EC_{number:03d}.json" for number in range(1, 51))
    actual = {path.name for path in input_dir.glob("*.json")}
    expected_names = {path.name for path in expected}
    if actual != expected_names:
        missing = sorted(expected_names - actual)
        extra = sorted(actual - expected_names)
        raise CaseContractError(f"Input directory must contain exactly EC_001..EC_050; missing={missing}, extra={extra}")
    return expected


OUTPUT_TOP_LEVEL_FIELDS = frozenset({
    "case_id", "case_assessment", "affected_entities", "customer_context",
    "product_context", "delivery_analysis", "payment_reconciliation",
    "root_cause_analysis", "evidence_ids", "financial_resolution", "resolution_actions",
})


def validate_output_envelope(document: Any, expected_case_id: str) -> None:
    """Validate invariant output shape now; semantic validation comes in CP3."""

    if not isinstance(document, dict) or set(document) != OUTPUT_TOP_LEVEL_FIELDS:
        raise CaseContractError("Output must contain exactly the 11 documented top-level fields")
    if document["case_id"] != expected_case_id:
        raise CaseContractError("Output case_id does not match its input case")


def validate_case_output(document: Any, expected_case_id: str) -> None:
    """Validate required output shape, type safety, and documented array caps."""

    validate_output_envelope(document, expected_case_id)
    _exact_fields(document["case_assessment"], {"primary_issue", "secondary_issues", "case_status", "confidence"}, "case_assessment")
    assessment = document["case_assessment"]
    if assessment["case_status"] not in {"action_required", "no_action"}:
        raise CaseContractError("Invalid case_status")
    if not isinstance(assessment["confidence"], (int, float)) or not 0 <= assessment["confidence"] <= 1:
        raise CaseContractError("confidence must be a number in [0, 1]")
    _limited_string_lists(document["affected_entities"], {"order_ids": 5, "item_ids": 5, "seller_ids": 3, "payment_ids": 5}, "affected_entities")
    _exact_fields(document["customer_context"], {"customer_unique_id", "related_order_ids"}, "customer_context")
    _string_list(document["customer_context"]["related_order_ids"], 5, "related_order_ids")
    _limited_string_lists(document["product_context"], {"product_ids": 5, "category_names": 5}, "product_context")
    _exact_fields(document["delivery_analysis"], {
        "delivered_at", "estimated_delivery_at", "carrier_handoff_at", "delivery_variance_hours",
        "seller_handoff_analysis", "late_handoff_seller_ids",
    }, "delivery_analysis")
    _string_list(document["delivery_analysis"]["late_handoff_seller_ids"], 3, "late_handoff_seller_ids")
    handoffs = document["delivery_analysis"]["seller_handoff_analysis"]
    if not isinstance(handoffs, list) or len(handoffs) > 3:
        raise CaseContractError("seller_handoff_analysis must contain at most 3 entries")
    _exact_fields(document["payment_reconciliation"], {
        "currency", "item_total_brl", "freight_total_brl", "expected_total_brl", "payment_total_brl",
        "difference_brl", "reconciled", "payment_types",
    }, "payment_reconciliation")
    _string_list(document["payment_reconciliation"]["payment_types"], None, "payment_types")
    _exact_fields(document["root_cause_analysis"], {"ranked_causes", "responsible_parties"}, "root_cause_analysis")
    for field, limit in (("ranked_causes", 3), ("responsible_parties", 3), ("evidence_ids", 20), ("resolution_actions", 5)):
        value = document["root_cause_analysis"][field] if field in {"ranked_causes", "responsible_parties"} else document[field]
        if not isinstance(value, list) or len(value) > limit:
            raise CaseContractError(f"{field} exceeds limit {limit}")
    _validate_evidence_id_format(document["evidence_ids"])
    _exact_fields(document["financial_resolution"], {"currency", "recommended_refund_brl"}, "financial_resolution")
    if document["financial_resolution"]["currency"] != "BRL":
        raise CaseContractError("financial_resolution currency must be BRL")


def _exact_fields(value: Any, required: set[str], name: str) -> None:
    if not isinstance(value, dict) or set(value) != required:
        raise CaseContractError(f"{name} has invalid fields")


def _limited_string_lists(container: Any, limits: dict[str, int], name: str) -> None:
    _exact_fields(container, set(limits), name)
    for field, limit in limits.items():
        _string_list(container[field], limit, f"{name}.{field}")


def _string_list(value: Any, limit: int | None, name: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CaseContractError(f"{name} must be a list of strings")
    if limit is not None and len(value) > limit:
        raise CaseContractError(f"{name} exceeds limit {limit}")


def _validate_evidence_id_format(evidence_ids: list[Any]) -> None:
    if len(set(evidence_ids)) != len(evidence_ids):
        raise CaseContractError("evidence_ids must not contain duplicates")
    for evidence_id in evidence_ids:
        if not isinstance(evidence_id, str):
            raise CaseContractError("evidence_ids must be strings")
        parts = evidence_id.split(":")
        if parts[0] in {"order", "seller", "policy"} and len(parts) == 2 and parts[1]:
            continue
        if parts[0] in {"item", "payment"} and len(parts) == 3 and all(parts[1:]):
            continue
        raise CaseContractError(f"Invalid evidence ID: {evidence_id}")
