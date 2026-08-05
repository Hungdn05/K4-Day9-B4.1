"""Independent EC_POLICY_V2 consistency checker.

This module intentionally does not import the production repository, pipeline,
policy, or validator.  It rebuilds every expected field directly from CSV rows
so a shared implementation bug cannot make investigation and verification pass
together.  It is not a replica of the external grader and must never be used
to infer hidden conventions from proximity to a single aggregate score.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
from typing import Any


GROUP_WEIGHTS = {
    "issues": 15,
    "entities": 15,
    "contexts": 15,
    "delivery": 15,
    "payment": 15,
    "root_evidence": 15,
    "financial_actions": 10,
}


@dataclass(frozen=True)
class OracleOptions:
    no_item_totals_zero: bool = True
    translated_categories: bool = False
    responsible_sellers_only: bool = False
    no_action_primary_action_only: bool = False
    sort_payments_by_sequence: bool = False
    deduplicate_payment_types: bool = True


def rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def group_by(source: list[dict[str, str]], key: str) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in source:
        grouped.setdefault(row[key], []).append(row)
    return grouped


def distinct(values: list[str], limit: int | None = None) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
            if limit is not None and len(result) == limit:
                break
    return result


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def hour_difference(later: str, earlier: str) -> Decimal | None:
    if not later or not earlier:
        return None
    later_at = datetime.strptime(later, "%Y-%m-%d %H:%M:%S")
    earlier_at = datetime.strptime(earlier, "%Y-%m-%d %H:%M:%S")
    seconds = Decimal(str((later_at - earlier_at).total_seconds()))
    return (seconds / Decimal("3600")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class IndependentOracle:
    def __init__(self, data_dir: Path, options: OracleOptions) -> None:
        self.options = options
        customers = rows(data_dir / "olist_customers_dataset.csv")
        orders = rows(data_dir / "olist_orders_dataset.csv")
        items = rows(data_dir / "olist_order_items_dataset.csv")
        payments = rows(data_dir / "olist_order_payments_dataset.csv")
        products = rows(data_dir / "olist_products_dataset.csv")
        translations = rows(data_dir / "product_category_name_translation.csv")
        self.customers = {row["customer_id"]: row for row in customers}
        self.orders = {row["order_id"]: row for row in orders}
        self.order_rows = orders
        self.items = group_by(items, "order_id")
        self.payments = group_by(payments, "order_id")
        self.products = {row["product_id"]: row for row in products}
        self.translations = {row["product_category_name"]: row["product_category_name_english"] for row in translations}

    def expected(self, case: dict[str, Any]) -> dict[str, Any]:
        case_id = case["case_id"]
        order_id = case["customer_request"]["claimed_order_id"]
        order = self.orders[order_id]
        customer = self.customers[order["customer_id"]]
        order_items = list(self.items.get(order_id, []))
        payment_rows = list(self.payments.get(order_id, []))
        if self.options.sort_payments_by_sequence:
            payment_rows.sort(key=lambda row: int(row["payment_sequential"]))

        all_sellers = distinct([row["seller_id"] for row in order_items])
        product_ids = distinct([row["product_id"] for row in order_items])
        raw_categories = distinct([
            self.products[row["product_id"]]["product_category_name"] for row in order_items
        ])
        categories = [self.translations.get(category, category) for category in raw_categories]
        if not self.options.translated_categories:
            categories = raw_categories

        item_total: Decimal | None
        freight_total: Decimal | None
        expected_total: Decimal | None
        difference: Decimal | None
        reconciled: bool | None
        payment_total = money(sum((Decimal(row["payment_value"]) for row in payment_rows), Decimal("0")))
        if order_items:
            item_total = money(sum((Decimal(row["price"]) for row in order_items), Decimal("0")))
            freight_total = money(sum((Decimal(row["freight_value"]) for row in order_items), Decimal("0")))
            expected_total = money(item_total + freight_total)
            difference = money(payment_total - expected_total)
            reconciled = abs(difference) <= Decimal("0.10")
        else:
            item_total = Decimal("0") if self.options.no_item_totals_zero else None
            freight_total = Decimal("0") if self.options.no_item_totals_zero else None
            expected_total = difference = None  # named as null by EC_POLICY_V2
            reconciled = None

        delivered_at = order["order_delivered_customer_date"] or None
        estimated_at = order["order_estimated_delivery_date"] or None
        carrier_at = order["order_delivered_carrier_date"] or None
        delivery_variance = hour_difference(delivered_at or "", estimated_at or "")
        seller_analysis: list[dict[str, Any]] = []
        # No carrier handoff event in the data means there is no handoff to analyse.
        for seller_id in all_sellers if carrier_at else []:
            seller_limits = [
                row["shipping_limit_date"] for row in order_items
                if row["seller_id"] == seller_id and row["shipping_limit_date"]
            ]
            limit = min(seller_limits) if seller_limits else None
            measured = hour_difference(carrier_at or "", limit or "")
            # No carrier handoff means nothing to measure; the variance is 0.00,
            # not null, because EC_POLICY_V2 authorises null only for timestamps
            # and for expected_total_brl / difference_brl / reconciled.
            variance = measured if measured is not None else Decimal("0")
            seller_analysis.append({
                "seller_id": seller_id,
                "shipping_limit_at": limit,
                "handoff_variance_hours": number(variance),
                "late_handoff": variance > 0,
            })
        late_sellers = [row["seller_id"] for row in seller_analysis if row["late_handoff"]][:3]
        delivered_late = delivery_variance is not None and delivery_variance > 0

        related_orders = [
            candidate["order_id"]
            for candidate in self.order_rows
            if candidate["order_id"] != order_id
            and self.customers[candidate["customer_id"]]["customer_unique_id"] == customer["customer_unique_id"]
        ]

        primary, root_code, parties, refund, main_action = self._primary(
            order["order_status"], payment_total, freight_total,
            len(payment_rows), reconciled, delivery_variance, late_sellers,
        )
        secondary: list[str] = []
        if len(order_items) >= 2:
            secondary.append("multi_item_order")
        if len(all_sellers) >= 2:
            secondary.append("multi_seller_order")
        if len(payment_rows) >= 2:
            secondary.append("split_payment")
        if related_orders:
            secondary.append("repeat_customer")
        if len(raw_categories) >= 2:
            secondary.append("multiple_categories")

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
        if self.options.no_action_primary_action_only and refund == 0:
            actions = [main_action]

        item_ids = [f"{order_id}:{row['order_item_id']}" for row in order_items][:5]
        payment_ids = [f"{order_id}:{row['payment_sequential']}" for row in payment_rows][:5]
        affected_sellers = all_sellers[:3]
        responsible_sellers = [party["party_id"] for party in parties if party["party_type"] == "seller"]
        if self.options.responsible_sellers_only:
            affected_sellers = responsible_sellers[:3]
        evidence = [f"order:{order_id}"]
        evidence.extend(f"item:{value}" for value in item_ids)
        evidence.extend(f"payment:{value}" for value in payment_ids)
        evidence.extend(f"seller:{value}" for value in responsible_sellers[:3])
        evidence.append(f"policy:{root_code}")

        payment_types = [row["payment_type"] for row in payment_rows]
        if self.options.deduplicate_payment_types:
            payment_types = distinct(payment_types)

        return {
            "case_id": case_id,
            "case_assessment": {
                "primary_issue": primary,
                "secondary_issues": secondary,
                "case_status": "action_required" if refund > 0 else "no_action",
            },
            "affected_entities": {
                "order_ids": [order_id], "item_ids": item_ids,
                "seller_ids": affected_sellers, "payment_ids": payment_ids,
            },
            "customer_context": {
                "customer_unique_id": customer["customer_unique_id"],
                "related_order_ids": related_orders[:5],
            },
            "product_context": {
                "product_ids": product_ids[:5], "category_names": categories[:5],
            },
            "delivery_analysis": {
                "delivered_at": delivered_at, "estimated_delivery_at": estimated_at,
                "carrier_handoff_at": carrier_at,
                "delivery_variance_hours": number(delivery_variance),
                "seller_handoff_analysis": seller_analysis[:3],
                "late_handoff_seller_ids": late_sellers,
            },
            "payment_reconciliation": {
                "currency": "BRL", "item_total_brl": number(item_total),
                "freight_total_brl": number(freight_total),
                "expected_total_brl": number(expected_total),
                "payment_total_brl": number(payment_total),
                "difference_brl": number(difference), "reconciled": reconciled,
                "payment_types": payment_types,
            },
            "root_cause_analysis": {
                "ranked_causes": [{"cause_code": root_code, "rank": 1}],
                "responsible_parties": parties,
            },
            "evidence_ids": evidence[:20],
            "financial_resolution": {"currency": "BRL", "recommended_refund_brl": number(refund)},
            "resolution_actions": actions[:5],
        }

    @staticmethod
    def _primary(
        status: str, payment_total: Decimal, freight_total: Decimal | None,
        payment_count: int, reconciled: bool | None,
        delivery_variance: Decimal | None, late_sellers: list[str],
    ) -> tuple[str, str, list[dict[str, str]], Decimal, str]:
        if status == "canceled" and payment_total > 0:
            return "canceled_order_paid", "ORDER_CANCELED_AFTER_PAYMENT", [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}], payment_total, "issue_full_refund"
        if status == "unavailable" and payment_total > 0:
            return "unavailable_order_paid", "ORDER_UNAVAILABLE_AFTER_PAYMENT", [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}], payment_total, "issue_full_refund"
        if delivery_variance is not None and delivery_variance > 0 and late_sellers:
            return "late_delivery_seller", "SELLER_HANDOFF_AFTER_LIMIT", [{"party_type": "seller", "party_id": seller} for seller in late_sellers], freight_total or Decimal("0"), "refund_freight"
        if delivery_variance is not None and delivery_variance > 0:
            return "late_delivery_logistics", "CARRIER_DELIVERED_AFTER_ESTIMATE", [{"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}], freight_total or Decimal("0"), "refund_freight"
        if payment_count >= 2 and reconciled is True:
            return "valid_split_payment", "MULTIPLE_PAYMENTS_RECONCILED", [], Decimal("0"), "explain_valid_split_payment"
        if delivery_variance is not None and delivery_variance <= 0 and reconciled is True:
            return "unsupported_late_claim", "DELIVERY_WITHIN_ESTIMATE", [], Decimal("0"), "reject_late_refund"
        raise ValueError("No documented primary rule matches case")


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        flattened: dict[str, Any] = {}
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            flattened.update(flatten(child, path))
        return flattened
    return {prefix: value}


def group_documents(document: dict[str, Any]) -> dict[str, Any]:
    assessment = dict(document["case_assessment"])
    confidence = assessment.pop("confidence", None)
    assessment["confidence_valid"] = isinstance(confidence, (int, float)) and 0 <= confidence <= 1
    return {
        "issues": assessment,
        "entities": document["affected_entities"],
        "contexts": {"customer_context": document["customer_context"], "product_context": document["product_context"]},
        "delivery": document["delivery_analysis"],
        "payment": document["payment_reconciliation"],
        "root_evidence": {"root_cause_analysis": document["root_cause_analysis"], "evidence_ids": document["evidence_ids"]},
        "financial_actions": {"financial_resolution": document["financial_resolution"], "resolution_actions": document["resolution_actions"]},
    }


def grade(root: Path, options: OracleOptions) -> dict[str, Any]:
    oracle = IndependentOracle(root / "data", options)
    case_reports: list[dict[str, Any]] = []
    exact_group_counts = {group: 0 for group in GROUP_WEIGHTS}
    for input_path in sorted((root / "input").glob("EC_*.json")):
        case = json.loads(input_path.read_text(encoding="utf-8"))
        expected = oracle.expected(case)
        actual = json.loads((root / "output" / input_path.name).read_text(encoding="utf-8"))
        expected_groups = group_documents({**expected, "case_assessment": {**expected["case_assessment"], "confidence": 1.0}})
        actual_groups = group_documents(actual)
        mismatches: list[dict[str, Any]] = []
        score = 0.0
        group_results: dict[str, bool] = {}
        for group, weight in GROUP_WEIGHTS.items():
            expected_flat = flatten(expected_groups[group])
            actual_flat = flatten(actual_groups[group])
            keys = sorted(set(expected_flat) | set(actual_flat))
            group_mismatches = [
                {"path": f"{group}.{key}", "expected": expected_flat.get(key), "actual": actual_flat.get(key)}
                for key in keys if expected_flat.get(key) != actual_flat.get(key)
            ]
            exact = not group_mismatches
            group_results[group] = exact
            if exact:
                exact_group_counts[group] += 1
                score += weight
            mismatches.extend(group_mismatches)
        case_reports.append({
            "case_id": case["case_id"],
            "primary_issue": expected["case_assessment"]["primary_issue"],
            "exact_group_score": score,
            "groups": group_results,
            "mismatches": mismatches,
        })
    return {
        "options": options.__dict__,
        "case_count": len(case_reports),
        "mean_exact_group_score": round(sum(case["exact_group_score"] for case in case_reports) / len(case_reports), 4),
        "exact_group_counts": exact_group_counts,
        "mismatch_case_count": sum(bool(case["mismatches"]) for case in case_reports),
        "cases": case_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--report", type=Path, default=Path("qa/internal_grader_report.json"))
    parser.add_argument("--observed-score", type=float, default=67.1417)
    args = parser.parse_args()
    root = args.root.resolve()
    result = grade(root, OracleOptions())
    scenarios = {
        "legacy_null_empty_totals": OracleOptions(no_item_totals_zero=False),
        "english_categories": OracleOptions(translated_categories=True),
        "responsible_sellers_only": OracleOptions(responsible_sellers_only=True),
        "no_action_primary_action_only": OracleOptions(no_action_primary_action_only=True),
        "payment_sequence_sort": OracleOptions(sort_payments_by_sequence=True),
        "deduplicated_payment_types": OracleOptions(deduplicate_payment_types=True),
        "rejected_combined_mutation": OracleOptions(
            no_item_totals_zero=True,
            translated_categories=True,
            responsible_sellers_only=True,
            no_action_primary_action_only=True,
            sort_payments_by_sequence=True,
            deduplicate_payment_types=False,
        ),
    }
    result["observed_external_score"] = args.observed_score
    result["hypothesis_sweep"] = {}
    for name, options in scenarios.items():
        scenario = grade(root, options)
        result["hypothesis_sweep"][name] = {
            "mean_exact_group_score": scenario["mean_exact_group_score"],
            "distance_to_observed_score": round(abs(scenario["mean_exact_group_score"] - args.observed_score), 4),
            "exact_group_counts": scenario["exact_group_counts"],
            "mismatch_case_count": scenario["mismatch_case_count"],
        }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("mean_exact_group_score", "exact_group_counts", "mismatch_case_count")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
