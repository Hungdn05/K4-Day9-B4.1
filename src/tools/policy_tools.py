"""Policy-domain tools.

These do not decide anything. The Policy Agent picks the branch; these tools turn its
choice into exact figures and exact identifiers, so a refund is never a number the
model typed and an evidence id is never a string the model invented.
"""

from __future__ import annotations

from src import config, policy
from src.data.store import get_store
from src.tools.calc import dec_sum, round2
from src.tools.registry import ToolError, make_toolbox


def _require_order(order_id: str) -> None:
    if get_store().get_order(order_id) is None:
        raise ToolError(f"order_id {order_id!r} does not exist in olist_orders_dataset.csv")


def compute_refund_amount(order_id: str, refund_basis: str) -> dict:
    """Turn a refund basis into the exact BRL figure for that order."""
    _require_order(order_id)
    valid = {"payment_total", "freight_total", "none"}
    if refund_basis not in valid:
        raise ToolError(f"refund_basis must be one of {sorted(valid)}, got {refund_basis!r}")

    store = get_store()
    items = store.get_items(order_id)
    payments = store.get_payments(order_id)

    if refund_basis == "payment_total":
        amount = round2(dec_sum(r["payment_value"] for r in payments))
    elif refund_basis == "freight_total":
        amount = round2(dec_sum(r["freight_value"] for r in items)) if items else 0.0
    else:
        amount = 0.0

    return {
        "order_id": order_id,
        "currency": config.CURRENCY,
        "refund_basis": refund_basis,
        "recommended_refund_brl": amount,
        "case_status": "action_required" if amount > 0 else "no_action",
    }


def build_evidence_ids(
    order_id: str,
    root_cause_code: str,
    responsible_seller_ids: list[str] | None = None,
) -> dict:
    """Build the evidence id list from real rows: order, items, payments, sellers, policy.

    Every id is constructed from data that exists in the CSVs, so it cannot be a false
    positive. Seller ids are validated against olist_sellers_dataset.csv.
    """
    _require_order(order_id)
    if root_cause_code not in policy.ROOT_CAUSE_CODES:
        raise ToolError(
            f"root_cause_code must be one of {list(policy.ROOT_CAUSE_CODES)}, got {root_cause_code!r}"
        )

    store = get_store()
    items = store.get_items(order_id)
    payments = store.get_payments(order_id)
    order_sellers = [row["seller_id"] for row in items]

    evidence = [f"order:{order_id}"]
    evidence += [f"item:{order_id}:{row['order_item_id']}" for row in items]
    evidence += [f"payment:{order_id}:{row['payment_sequential']}" for row in payments]

    rejected: list[str] = []
    accepted: list[str] = []
    for seller_id in responsible_seller_ids or []:
        if seller_id in order_sellers and seller_id not in accepted:
            accepted.append(seller_id)
            evidence.append(f"seller:{seller_id}")
        elif seller_id not in order_sellers:
            rejected.append(seller_id)

    evidence.append(f"policy:{root_cause_code}")

    limit = config.ARRAY_LIMITS["evidence_ids"]
    return {
        "order_id": order_id,
        "evidence_ids": evidence[:limit],
        "evidence_count": min(len(evidence), limit),
        "truncated": len(evidence) > limit,
        "rejected_seller_ids": rejected,
        "note": (
            f"seller ids {rejected} are not sellers on this order and were dropped"
            if rejected
            else ""
        ),
    }


POLICY_TOOLS = make_toolbox(
    "policy",
    [
        (
            compute_refund_amount,
            "Compute the exact recommended refund for a chosen basis. Use 'payment_total' "
            "for canceled_order_paid and unavailable_order_paid, 'freight_total' for both "
            "late_delivery issues, and 'none' for valid_split_payment and "
            "unsupported_late_claim. Never compute a refund yourself.",
            {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "The order under investigation"},
                    "refund_basis": {
                        "type": "string",
                        "enum": ["payment_total", "freight_total", "none"],
                        "description": "Which basis the policy table prescribes for the chosen primary issue",
                    },
                },
                "required": ["order_id", "refund_basis"],
                "additionalProperties": False,
            },
        ),
        (
            build_evidence_ids,
            "Build the full evidence_ids list for the case from real CSV rows. Pass the "
            "seller ids you hold responsible (empty when nobody is responsible) and the "
            "root cause code you selected. Never write evidence ids by hand.",
            {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "The order under investigation"},
                    "root_cause_code": {
                        "type": "string",
                        "enum": list(policy.ROOT_CAUSE_CODES),
                        "description": "The rank-1 root cause code for this case",
                    },
                    "responsible_seller_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Seller ids held responsible; empty when nobody is",
                    },
                },
                "required": ["order_id", "root_cause_code"],
                "additionalProperties": False,
            },
        ),
    ],
)
