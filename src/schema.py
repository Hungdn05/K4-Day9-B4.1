"""Output schema and assembly.

`build_output` welds two things together:
  * FACTS -- copied verbatim out of the tool results the specialists collected,
  * DECISION -- what the Policy Agent concluded.

No number in the result is ever produced by a model. If a field can be derived from
the CSVs it comes from `facts`; if it requires judgment it comes from `decision`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src import config, policy


# --- pydantic contract -------------------------------------------------------
class CaseAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_issue: Literal[policy.PRIMARY_ISSUES]  # type: ignore[valid-type]
    secondary_issues: list[str]
    case_status: Literal["action_required", "no_action"]
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("secondary_issues")
    @classmethod
    def known_secondary(cls, value: list[str]) -> list[str]:
        unknown = [v for v in value if v not in policy.SECONDARY_ISSUE_ORDER]
        if unknown:
            raise ValueError(f"unknown secondary issues: {unknown}")
        return value


class AffectedEntities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_ids: list[str]
    item_ids: list[str]
    seller_ids: list[str]
    payment_ids: list[str]


class CustomerContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_unique_id: str
    related_order_ids: list[str]


class ProductContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_ids: list[str]
    category_names: list[str]


class SellerHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seller_id: str
    shipping_limit_at: str | None
    handoff_variance_hours: float | None
    late_handoff: bool


class DeliveryAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivered_at: str | None
    estimated_delivery_at: str | None
    carrier_handoff_at: str | None
    delivery_variance_hours: float | None
    seller_handoff_analysis: list[SellerHandoff]
    late_handoff_seller_ids: list[str]


class PaymentReconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str
    item_total_brl: float | None
    freight_total_brl: float | None
    expected_total_brl: float | None
    payment_total_brl: float | None
    difference_brl: float | None
    reconciled: bool | None
    payment_types: list[str]


class RankedCause(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cause_code: Literal[policy.ROOT_CAUSE_CODES]  # type: ignore[valid-type]
    rank: int = Field(ge=1)


class ResponsibleParty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    party_type: Literal["platform", "seller", "logistics_provider"]
    party_id: str


class RootCauseAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ranked_causes: list[RankedCause]
    responsible_parties: list[ResponsibleParty]


class FinancialResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str
    recommended_refund_brl: float


class CaseOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    case_assessment: CaseAssessment
    affected_entities: AffectedEntities
    customer_context: CustomerContext
    product_context: ProductContext
    delivery_analysis: DeliveryAnalysis
    payment_reconciliation: PaymentReconciliation
    root_cause_analysis: RootCauseAnalysis
    evidence_ids: list[str]
    financial_resolution: FinancialResolution
    resolution_actions: list[str]


# --- assembly ----------------------------------------------------------------
def _cap(values: list[Any], key: str) -> list[Any]:
    return list(values)[: config.ARRAY_LIMITS[key]]


def build_output(case_id: str, order_id: str, facts: dict[str, Any], decision: dict[str, Any]) -> dict:
    """Assemble the final case JSON from collected facts plus the policy decision."""
    items = facts.get("list_order_items", {})
    products = facts.get("get_order_product_context", {})
    payments = facts.get("list_order_payments", {})
    recon = facts.get("reconcile_order_payment", {})
    timeline = facts.get("get_delivery_timeline", {})
    handoff = facts.get("analyze_seller_handoff", {})
    identity = facts.get("lookup_customer_identity", {})
    history = facts.get("list_customer_order_history", {})

    primary = decision["primary_issue"]
    refund = float(decision["recommended_refund_brl"])

    ranked = [
        {"cause_code": code, "rank": index}
        for index, code in enumerate(_cap(decision.get("ranked_cause_codes", []), "ranked_causes"), 1)
    ]

    output = {
        "case_id": case_id,
        "case_assessment": {
            "primary_issue": primary,
            "secondary_issues": decision.get("secondary_issues", []),
            "case_status": "action_required" if refund > 0 else "no_action",
            "confidence": round(float(decision.get("confidence", 0.9)), 2),
        },
        "affected_entities": {
            "order_ids": [order_id],
            "item_ids": _cap(items.get("item_ids", []), "item_ids"),
            "seller_ids": _cap(items.get("seller_ids", []), "seller_ids"),
            "payment_ids": _cap(payments.get("payment_ids", []), "payment_ids"),
        },
        "customer_context": {
            "customer_unique_id": identity.get("customer_unique_id", ""),
            "related_order_ids": _cap(history.get("related_order_ids", []), "related_order_ids"),
        },
        "product_context": {
            "product_ids": _cap(products.get("product_ids", []), "product_ids"),
            "category_names": _cap(products.get("category_names", []), "category_names"),
        },
        "delivery_analysis": {
            "delivered_at": timeline.get("delivered_at"),
            "estimated_delivery_at": timeline.get("estimated_delivery_at"),
            "carrier_handoff_at": timeline.get("carrier_handoff_at"),
            "delivery_variance_hours": timeline.get("delivery_variance_hours"),
            "seller_handoff_analysis": handoff.get("seller_handoff_analysis", []),
            "late_handoff_seller_ids": handoff.get("late_handoff_seller_ids", []),
        },
        "payment_reconciliation": {
            "currency": config.CURRENCY,
            "item_total_brl": recon.get("item_total_brl"),
            "freight_total_brl": recon.get("freight_total_brl"),
            "expected_total_brl": recon.get("expected_total_brl"),
            "payment_total_brl": recon.get("payment_total_brl"),
            "difference_brl": recon.get("difference_brl"),
            "reconciled": recon.get("reconciled"),
            "payment_types": payments.get("payment_types", []),
        },
        "root_cause_analysis": {
            "ranked_causes": ranked,
            "responsible_parties": _cap(
                decision.get("responsible_parties", []), "responsible_parties"
            ),
        },
        "evidence_ids": _cap(decision.get("evidence_ids", []), "evidence_ids"),
        "financial_resolution": {
            "currency": config.CURRENCY,
            "recommended_refund_brl": refund,
        },
        "resolution_actions": _cap(decision.get("resolution_actions", []), "resolution_actions"),
    }
    return output


def validate_structure(output: dict) -> list[str]:
    """Return pydantic errors as flat strings; empty list means the shape is legal."""
    try:
        CaseOutput.model_validate(output)
    except Exception as exc:  # pydantic ValidationError
        errors = getattr(exc, "errors", None)
        if callable(errors):
            return [
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in errors()
            ]
        return [str(exc)]
    return []
