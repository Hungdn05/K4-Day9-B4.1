"""Deterministic implementation of primary/secondary rules in EC_POLICY_V2."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class PolicyResolutionError(ValueError):
    """Raised if documented policy cannot be applied to the provided facts."""


@dataclass(frozen=True)
class CaseFacts:
    """Facts normalized by domain agents, never inferred by the policy agent."""

    order_status: str
    payment_total_brl: Decimal
    freight_total_brl: Decimal | None
    item_count: int
    seller_ids: tuple[str, ...]
    payment_count: int
    reconciled: bool | None
    delivered_late: bool | None
    late_handoff_seller_ids: tuple[str, ...]
    is_repeat_customer: bool
    category_count: int


@dataclass(frozen=True)
class ResponsibleParty:
    party_type: str
    party_id: str


@dataclass(frozen=True)
class PolicyDecision:
    primary_issue: str
    secondary_issues: tuple[str, ...]
    root_cause_code: str
    responsible_parties: tuple[ResponsibleParty, ...]
    recommended_refund_brl: Decimal
    case_status: str
    resolution_actions: tuple[str, ...]


def decide_policy(facts: CaseFacts) -> PolicyDecision:
    """Apply the documented primary-rule precedence and ordered additions."""

    primary, root_cause, responsible, refund, main_action = _primary_decision(facts)
    secondary = _secondary_issues(facts)
    actions = [main_action]
    if primary == "late_delivery_seller":
        actions.append("review_seller_handoff")
    elif primary == "late_delivery_logistics":
        actions.append("review_carrier_delay")
    if refund > 0:
        actions.append("verify_refund_completion")
    if "multi_seller_order" in secondary:
        actions.append("coordinate_multi_seller_case")
    if "split_payment" in secondary and primary != "valid_split_payment":
        actions.append("verify_payment_allocation")
    return PolicyDecision(
        primary_issue=primary,
        secondary_issues=secondary,
        root_cause_code=root_cause,
        responsible_parties=responsible,
        recommended_refund_brl=refund,
        case_status="action_required" if refund > 0 else "no_action",
        resolution_actions=tuple(actions),
    )


def _primary_decision(
    facts: CaseFacts,
) -> tuple[str, str, tuple[ResponsibleParty, ...], Decimal, str]:
    # Ordered exactly as README section 4.
    if facts.order_status == "canceled" and facts.payment_total_brl > 0:
        return (
            "canceled_order_paid", "ORDER_CANCELED_AFTER_PAYMENT",
            (ResponsibleParty("platform", "OLIST_PLATFORM"),), facts.payment_total_brl,
            "issue_full_refund",
        )
    if facts.order_status == "unavailable" and facts.payment_total_brl > 0:
        return (
            "unavailable_order_paid", "ORDER_UNAVAILABLE_AFTER_PAYMENT",
            (ResponsibleParty("platform", "OLIST_PLATFORM"),), facts.payment_total_brl,
            "issue_full_refund",
        )
    if facts.delivered_late is True and facts.late_handoff_seller_ids:
        return (
            "late_delivery_seller", "SELLER_HANDOFF_AFTER_LIMIT",
            tuple(ResponsibleParty("seller", seller_id) for seller_id in facts.late_handoff_seller_ids),
            _freight_or_error(facts), "refund_freight",
        )
    if facts.delivered_late is True and not facts.late_handoff_seller_ids:
        return (
            "late_delivery_logistics", "CARRIER_DELIVERED_AFTER_ESTIMATE",
            (ResponsibleParty("logistics_provider", "LOGISTICS_PROVIDER"),),
            _freight_or_error(facts), "refund_freight",
        )
    if facts.payment_count >= 2 and facts.reconciled is True:
        return (
            "valid_split_payment", "MULTIPLE_PAYMENTS_RECONCILED", (), Decimal("0"),
            "explain_valid_split_payment",
        )
    if facts.delivered_late is False and facts.reconciled is True:
        return (
            "unsupported_late_claim", "DELIVERY_WITHIN_ESTIMATE", (), Decimal("0"),
            "reject_late_refund",
        )
    raise PolicyResolutionError("No EC_POLICY_V2 primary issue can be proven from supplied facts")


def _freight_or_error(facts: CaseFacts) -> Decimal:
    if facts.freight_total_brl is None:
        raise PolicyResolutionError("A late-delivery refund requires freight total")
    return facts.freight_total_brl


def _secondary_issues(facts: CaseFacts) -> tuple[str, ...]:
    secondary: list[str] = []
    if facts.item_count >= 2:
        secondary.append("multi_item_order")
    if len(set(facts.seller_ids)) >= 2:
        secondary.append("multi_seller_order")
    if facts.payment_count >= 2:
        secondary.append("split_payment")
    if facts.is_repeat_customer:
        secondary.append("repeat_customer")
    if facts.category_count >= 2:
        secondary.append("multiple_categories")
    return tuple(secondary)
