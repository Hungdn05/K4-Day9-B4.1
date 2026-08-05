"""CP3 accuracy check: cross-examine every specialist judgment against the tools.

The specialists are supposed to interpret, not to invent. This script recomputes the
ground truth deterministically and diffs it against what each agent reported, so a
model that reads the data and then states the opposite is caught immediately.

Usage:  python -m scripts.validate_specialists [CASE_ID ...]
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter

from src import config
from src.agents.specialists import SPECIALISTS
from src.data.store import get_store
from src.llm.client import shared_client
from src.tools.delivery_tools import analyze_seller_handoff, get_delivery_timeline
from src.tools.order_tools import get_order_core, get_order_product_context, list_order_items
from src.tools.payment_tools import list_order_payments, reconcile_order_payment

# One case per policy branch plus the awkward shapes, chosen at CP2 from the data.
DEFAULT_CASES = [
    "EC_001",  # on time, single payment      -> unsupported_late_claim
    "EC_002",  # late + seller handoff late   -> late_delivery_seller
    "EC_005",  # late, carrier only, 2 sellers-> late_delivery_logistics
    "EC_008",  # multi-seller + split payment
    "EC_012",  # unavailable, ZERO item rows
    "EC_016",  # late + split payment
]


def ground_truth(order_id: str) -> dict:
    store = get_store()
    core = get_order_core(order_id)
    items = list_order_items(order_id)
    prod = get_order_product_context(order_id)
    pay = list_order_payments(order_id)
    rec = reconcile_order_payment(order_id)
    tl = get_delivery_timeline(order_id)
    hf = analyze_seller_handoff(order_id)

    customer = store.get_customer(store.get_order(order_id)["customer_id"])
    history = store.get_orders_for_customer_unique(customer["customer_unique_id"])
    related = [row["order_id"] for row in history if row["order_id"] != order_id]

    if rec["reconciled"] is None:
        recon_status = "not_applicable"
    else:
        recon_status = "reconciled" if rec["reconciled"] else "not_reconciled"

    return {
        "customer_agent": {
            "is_repeat_customer": len(related) > 0,
            "related_order_count": len(related),
        },
        "order_product_agent": {
            "order_status": core["order_status"],
            "has_item_rows": core["has_item_rows"],
            "is_multi_item_order": items["is_multi_item_order"],
            "is_multi_seller_order": items["is_multi_seller_order"],
            "has_multiple_categories": prod["has_multiple_categories"],
        },
        "payment_agent": {
            "payment_row_count": pay["payment_row_count"],
            "is_split_payment": pay["is_split_payment"],
            "reconciliation_status": recon_status,
            "payment_total_is_positive": pay["payment_total_brl"] > 0,
        },
        "delivery_agent": {
            "delivered_late": tl["delivered_late"],
            "was_delivered": tl["delivered_at"] is not None,
            "any_late_handoff": hf["any_late_handoff"],
            "late_handoff_seller_ids": hf["late_handoff_seller_ids"],
        },
    }


def main() -> int:
    case_ids = sys.argv[1:] or DEFAULT_CASES
    started = time.time()
    field_stats: Counter[str] = Counter()
    mismatches: list[str] = []
    failures: list[str] = []

    for case_id in case_ids:
        case = json.loads((config.INPUT_DIR / f"{case_id}.json").read_text())
        order_id = case["customer_request"]["claimed_order_id"]
        truth = ground_truth(order_id)
        task = (
            f"Case {case_id}. The customer wrote: \"{case['customer_request']['message']}\"\n"
            f"The order under investigation is order_id={order_id}.\n"
            "Investigate your domain and file your findings."
        )

        print(f"\n{case_id}  ({truth['order_product_agent']['order_status']})")
        for name, agent in SPECIALISTS.items():
            result = agent.run(task)
            if result.status != "ok":
                failures.append(f"{case_id}/{name}: {result.status} - {result.error}")
                print(f"  {name:22s} STATUS={result.status}  {result.error}")
                continue

            bad = []
            for field, expected in truth[name].items():
                actual = result.findings.get(field)
                field_stats[f"{name}.{field}.total"] += 1
                if actual == expected:
                    field_stats[f"{name}.{field}.ok"] += 1
                else:
                    bad.append(f"{field}: got {actual!r}, expected {expected!r}")
                    mismatches.append(f"{case_id}/{name}/{field}: {actual!r} != {expected!r}")

            retries = sum(1 for r in result.tool_calls if not r.ok)
            mark = "ok " if not bad else "XX "
            note = f" (recovered from {retries} rejected call)" if retries else ""
            print(f"  {mark}{name:22s} {result.steps} steps{note}")
            for line in bad:
                print(f"       {line}")

    total = sum(v for k, v in field_stats.items() if k.endswith(".total"))
    correct = sum(v for k, v in field_stats.items() if k.endswith(".ok"))

    print(f"\n{'=' * 68}")
    print(f"cases          : {len(case_ids)}")
    print(f"judgment fields: {correct}/{total} correct")
    print(f"agent failures : {len(failures)}")
    for line in failures:
        print(f"  - {line}")
    if mismatches:
        print(f"mismatches ({len(mismatches)}):")
        for line in mismatches:
            print(f"  - {line}")
    print(f"elapsed        : {time.time() - started:.1f}s")
    print(f"usage          : {json.dumps(shared_client().usage.as_dict())}")
    return 1 if (mismatches or failures) else 0


if __name__ == "__main__":
    raise SystemExit(main())
