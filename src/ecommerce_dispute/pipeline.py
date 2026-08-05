"""Coordinator and domain-worker implementation for one dispute case."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .contracts import CaseRequest, validate_case_output
from .handoffs import AgentHandoff, AgentName
from .policy import CaseFacts, PolicyDecision, decide_policy
from .repository import OlistRepository


MAX_ITEMS = 5
MAX_SELLERS = 3
MAX_PAYMENTS = 5
MAX_RELATED_ORDERS = 5
MAX_PRODUCTS = 5
MAX_CATEGORIES = 5
MONEY_QUANTUM = Decimal("0.01")
HOURS_QUANTUM = Decimal("0.01")


def _distinct(values: list[str], limit: int | None = None) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
            if limit is not None and len(result) == limit:
                break
    return result


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _timestamp(value: str) -> datetime | None:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S") if value else None


def _hours_after(later: str, earlier: str) -> Decimal | None:
    later_time, earlier_time = _timestamp(later), _timestamp(earlier)
    if later_time is None or earlier_time is None:
        return None
    seconds = Decimal(str((later_time - earlier_time).total_seconds()))
    return (seconds / Decimal("3600")).quantize(HOURS_QUANTUM, rounding=ROUND_HALF_UP)


class DisputeCoordinator:
    """Runs the domain workers, policy worker, and schema gate for one request."""

    def __init__(self, repository: OlistRepository) -> None:
        self.repository = repository

    def investigate(self, request: CaseRequest) -> tuple[dict[str, Any], tuple[AgentHandoff, ...]]:
        order = self.repository.order(request.claimed_order_id)
        # These domain agents share no mutable state and can investigate in parallel.
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="olist-agent") as executor:
            customer_future = executor.submit(self._customer_worker, request, order)
            order_future = executor.submit(self._order_product_worker, request, order)
            payment_future = executor.submit(self._payment_worker, request, order)
            delivery_future = executor.submit(self._delivery_worker, request, order)
            customer_handoff = customer_future.result()
            order_handoff = order_future.result()
            payment_handoff = payment_future.result()
            delivery_handoff = delivery_future.result()
        facts = self._facts(order, customer_handoff, order_handoff, payment_handoff, delivery_handoff)
        decision = decide_policy(facts)
        policy_handoff = AgentHandoff.create(
            request.case_id, AgentName.POLICY, self._policy_payload(decision),
        )
        output = self._assemble(
            request, order, customer_handoff, order_handoff, payment_handoff,
            delivery_handoff, decision,
        )
        self._verify_evidence(output, order, decision)
        validate_case_output(output, request.case_id)
        verifier_handoff = AgentHandoff.create(
            request.case_id, AgentName.VERIFIER,
            {"valid": True, "checks": ["schema", "array_limits", "evidence_format"]},
            tuple(output["evidence_ids"]),
        )
        coordinator_handoff = AgentHandoff.create(
            request.case_id, AgentName.COORDINATOR,
            {"status": "assembled_and_verified", "claimed_order_id": request.claimed_order_id},
            (f"order:{request.claimed_order_id}",),
        )
        return output, (
            customer_handoff, order_handoff, payment_handoff, delivery_handoff,
            policy_handoff, verifier_handoff, coordinator_handoff,
        )

    def _customer_worker(self, request: CaseRequest, order: dict[str, str]) -> AgentHandoff:
        customer = self.repository.customer_for_order(order["order_id"])
        all_orders = self.repository.orders_by_customer_unique_id[customer["customer_unique_id"]]
        related = [row["order_id"] for row in all_orders if row["order_id"] != order["order_id"]]
        payload = {
            "customer_unique_id": customer["customer_unique_id"],
            "related_order_ids": related[:MAX_RELATED_ORDERS],
            "has_other_orders": bool(related),
        }
        return AgentHandoff.create(request.case_id, AgentName.CUSTOMER, payload, (f"order:{order['order_id']}",))

    def _order_product_worker(self, request: CaseRequest, order: dict[str, str]) -> AgentHandoff:
        items = self.repository.items_by_order_id.get(order["order_id"], ())
        item_ids = [f"{order['order_id']}:{item['order_item_id']}" for item in items]
        seller_ids = _distinct([item["seller_id"] for item in items])
        product_ids = _distinct([item["product_id"] for item in items])
        categories = _distinct([
            self.repository.product(item["product_id"]).get("product_category_name", "") for item in items
        ])
        payload = {
            "item_ids": item_ids[:MAX_ITEMS], "all_item_count": len(items),
            "seller_ids": seller_ids[:MAX_SELLERS], "all_seller_ids": seller_ids,
            "product_ids": product_ids[:MAX_PRODUCTS], "category_names": categories[:MAX_CATEGORIES],
            "all_category_count": len(categories),
            "items": [dict(item) for item in items],
        }
        sources = tuple(f"item:{item_id}" for item_id in item_ids[:MAX_ITEMS])
        return AgentHandoff.create(request.case_id, AgentName.ORDER_PRODUCT, payload, sources)

    def _payment_worker(self, request: CaseRequest, order: dict[str, str]) -> AgentHandoff:
        order_id = order["order_id"]
        items = self.repository.items_by_order_id.get(order_id, ())
        payments = self.repository.payments_by_order_id.get(order_id, ())
        payment_ids = [f"{order_id}:{payment['payment_sequential']}" for payment in payments]
        payment_total = _money(sum((Decimal(row["payment_value"]) for row in payments), Decimal("0")))
        if items:
            item_total = _money(sum((Decimal(row["price"]) for row in items), Decimal("0")))
            freight_total = _money(sum((Decimal(row["freight_value"]) for row in items), Decimal("0")))
            expected_total = _money(item_total + freight_total)
            difference = _money(payment_total - expected_total)
            reconciled: bool | None = abs(difference) <= Decimal("0.10")
        else:
            item_total = freight_total = expected_total = difference = None
            reconciled = None
        payload = {
            "payment_ids": payment_ids[:MAX_PAYMENTS], "all_payment_count": len(payments),
            "payment_types": _distinct([payment["payment_type"] for payment in payments]),
            "item_total_brl": item_total, "freight_total_brl": freight_total,
            "expected_total_brl": expected_total, "payment_total_brl": payment_total,
            "difference_brl": difference, "reconciled": reconciled,
        }
        sources = tuple(f"payment:{payment_id}" for payment_id in payment_ids[:MAX_PAYMENTS])
        return AgentHandoff.create(request.case_id, AgentName.PAYMENT, payload, sources)

    def _delivery_worker(
        self, request: CaseRequest, order: dict[str, str],
    ) -> AgentHandoff:
        items = self.repository.items_by_order_id.get(order["order_id"], ())
        delivered_at = order["order_delivered_customer_date"] or None
        estimated_at = order["order_estimated_delivery_date"] or None
        carrier_at = order["order_delivered_carrier_date"] or None
        seller_rows: list[dict[str, Any]] = []
        for seller_id in _distinct([item["seller_id"] for item in items]):
            seller_items = [item for item in items if item["seller_id"] == seller_id]
            shipping_limits = [item["shipping_limit_date"] for item in seller_items if item["shipping_limit_date"]]
            shipping_limit = min(shipping_limits) if shipping_limits else None
            handoff_variance = _hours_after(carrier_at or "", shipping_limit or "")
            seller_rows.append({
                "seller_id": seller_id,
                "shipping_limit_at": shipping_limit,
                "handoff_variance_hours": handoff_variance,
                "late_handoff": handoff_variance is not None and handoff_variance > 0,
            })
        late_sellers = [row["seller_id"] for row in seller_rows if row["late_handoff"]]
        delivery_variance = _hours_after(delivered_at or "", estimated_at or "")
        payload = {
            "delivered_at": delivered_at, "estimated_delivery_at": estimated_at,
            "carrier_handoff_at": carrier_at, "delivery_variance_hours": delivery_variance,
            "seller_handoff_analysis": seller_rows[:MAX_SELLERS],
            "late_handoff_seller_ids": late_sellers[:MAX_SELLERS],
            "delivered_late": delivery_variance is not None and delivery_variance > 0,
        }
        return AgentHandoff.create(request.case_id, AgentName.DELIVERY, payload, (f"order:{order['order_id']}",))

    @staticmethod
    def _facts(
        order: dict[str, str], customer: AgentHandoff, order_product: AgentHandoff,
        payment: AgentHandoff, delivery: AgentHandoff,
    ) -> CaseFacts:
        return CaseFacts(
            order_status=order["order_status"], payment_total_brl=payment.payload["payment_total_brl"],
            freight_total_brl=payment.payload["freight_total_brl"],
            item_count=order_product.payload["all_item_count"],
            seller_ids=tuple(order_product.payload["all_seller_ids"]),
            payment_count=payment.payload["all_payment_count"], reconciled=payment.payload["reconciled"],
            delivered_late=delivery.payload["delivered_late"] if delivery.payload["delivery_variance_hours"] is not None else None,
            late_handoff_seller_ids=tuple(delivery.payload["late_handoff_seller_ids"]),
            is_repeat_customer=customer.payload["has_other_orders"],
            category_count=order_product.payload["all_category_count"],
        )

    @staticmethod
    def _policy_payload(decision: PolicyDecision) -> dict[str, Any]:
        return {
            "primary_issue": decision.primary_issue,
            "root_cause_code": decision.root_cause_code,
            "recommended_refund_brl": str(decision.recommended_refund_brl),
        }

    def _assemble(
        self, request: CaseRequest, order: dict[str, str], customer: AgentHandoff,
        order_product: AgentHandoff, payment: AgentHandoff, delivery: AgentHandoff,
        decision: PolicyDecision,
    ) -> dict[str, Any]:
        order_data, payment_data, delivery_data = order_product.payload, payment.payload, delivery.payload
        responsible_sellers = [party.party_id for party in decision.responsible_parties if party.party_type == "seller"]
        evidence = [f"order:{order['order_id']}"]
        evidence.extend(f"item:{item_id}" for item_id in order_data["item_ids"])
        evidence.extend(f"payment:{payment_id}" for payment_id in payment_data["payment_ids"])
        evidence.extend(f"seller:{seller_id}" for seller_id in responsible_sellers[:MAX_SELLERS])
        evidence.append(f"policy:{decision.root_cause_code}")
        return {
            "case_id": request.case_id,
            "case_assessment": {
                "primary_issue": decision.primary_issue,
                "secondary_issues": list(decision.secondary_issues),
                "case_status": decision.case_status,
                "confidence": 0.98,
            },
            "affected_entities": {
                "order_ids": [order["order_id"]], "item_ids": order_data["item_ids"],
                "seller_ids": order_data["seller_ids"], "payment_ids": payment_data["payment_ids"],
            },
            "customer_context": {
                "customer_unique_id": customer.payload["customer_unique_id"],
                "related_order_ids": customer.payload["related_order_ids"],
            },
            "product_context": {
                "product_ids": order_data["product_ids"], "category_names": order_data["category_names"],
            },
            "delivery_analysis": {
                "delivered_at": delivery_data["delivered_at"],
                "estimated_delivery_at": delivery_data["estimated_delivery_at"],
                "carrier_handoff_at": delivery_data["carrier_handoff_at"],
                "delivery_variance_hours": _number(delivery_data["delivery_variance_hours"]),
                "seller_handoff_analysis": [
                    {
                        **row,
                        "handoff_variance_hours": _number(row["handoff_variance_hours"]),
                    }
                    for row in delivery_data["seller_handoff_analysis"]
                ],
                "late_handoff_seller_ids": delivery_data["late_handoff_seller_ids"],
            },
            "payment_reconciliation": {
                "currency": "BRL",
                "item_total_brl": _number(payment_data["item_total_brl"]),
                "freight_total_brl": _number(payment_data["freight_total_brl"]),
                "expected_total_brl": _number(payment_data["expected_total_brl"]),
                "payment_total_brl": _number(payment_data["payment_total_brl"]),
                "difference_brl": _number(payment_data["difference_brl"]),
                "reconciled": payment_data["reconciled"],
                "payment_types": payment_data["payment_types"],
            },
            "root_cause_analysis": {
                "ranked_causes": [{"cause_code": decision.root_cause_code, "rank": 1}],
                "responsible_parties": [
                    {"party_type": party.party_type, "party_id": party.party_id}
                    for party in decision.responsible_parties
                ],
            },
            "evidence_ids": evidence[:20],
            "financial_resolution": {
                "currency": "BRL", "recommended_refund_brl": _number(decision.recommended_refund_brl),
            },
            "resolution_actions": list(decision.resolution_actions),
        }

    @staticmethod
    def _verify_evidence(
        output: dict[str, Any], order: dict[str, str], decision: PolicyDecision,
    ) -> None:
        """Reconstruct every emitted evidence ID from collected source records."""

        expected = [f"order:{order['order_id']}"]
        expected.extend(f"item:{item_id}" for item_id in output["affected_entities"]["item_ids"])
        expected.extend(f"payment:{payment_id}" for payment_id in output["affected_entities"]["payment_ids"])
        expected.extend(
            f"seller:{party.party_id}" for party in decision.responsible_parties
            if party.party_type == "seller"
        )
        expected.append(f"policy:{decision.root_cause_code}")
        if output["evidence_ids"] != expected:
            raise ValueError("Verifier rejected evidence IDs not grounded in source records and policy")
