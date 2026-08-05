"""CP1 smoke test: data layer loads, indexes resolve, and the model answers.

Usage:  python -m scripts.smoke_test [CASE_ID]
"""

from __future__ import annotations

import json
import sys

from src import config
from src.data.store import get_store
from src.llm.client import shared_client


def profile_case(case_id: str) -> None:
    case = json.loads((config.INPUT_DIR / f"{case_id}.json").read_text())
    order_id = case["customer_request"]["claimed_order_id"]
    store = get_store()

    order = store.get_order(order_id)
    if order is None:
        print(f"  !! order {order_id} not found in orders CSV")
        return

    items = store.get_items(order_id)
    payments = store.get_payments(order_id)
    customer = store.get_customer(order["customer_id"])
    unique_id = customer["customer_unique_id"] if customer else None
    history = store.get_orders_for_customer_unique(unique_id) if unique_id else []
    related = [o["order_id"] for o in history if o["order_id"] != order_id]

    print(f"  order_id            : {order_id}")
    print(f"  order_status        : {order['order_status']}")
    print(f"  delivered_customer  : {order['order_delivered_customer_date']}")
    print(f"  estimated_delivery  : {order['order_estimated_delivery_date']}")
    print(f"  delivered_carrier   : {order['order_delivered_carrier_date']}")
    print(f"  item rows           : {len(items)}")
    print(f"  payment rows        : {len(payments)}")
    print(f"  customer_unique_id  : {unique_id}")
    print(f"  related order ids   : {related or '(none)'}")

    if items:
        sellers = sorted({i["seller_id"] for i in items})
        cats = sorted(
            {
                store.category_english(store.get_product(i["product_id"]).get("product_category_name"))
                for i in items
                if store.get_product(i["product_id"])
            }
            - {None}
        )
        item_total = round(sum(float(i["price"]) for i in items), 2)
        freight_total = round(sum(float(i["freight_value"]) for i in items), 2)
        print(f"  distinct sellers    : {len(sellers)} -> {sellers}")
        print(f"  categories          : {cats}")
        print(f"  item_total_brl      : {item_total}")
        print(f"  freight_total_brl   : {freight_total}")
    pay_total = round(sum(float(p["payment_value"]) for p in payments), 2)
    print(f"  payment_total_brl   : {pay_total}")
    print(f"  payment_types       : {[p['payment_type'] for p in payments]}")


def check_llm() -> None:
    client = shared_client()
    reply = client.chat(
        messages=[
            {
                "role": "system",
                "content": "You are a connectivity probe. Reply with JSON only.",
            },
            {
                "role": "user",
                "content": 'Return exactly {"status":"ok","model_family":"<your model family>"}',
            },
        ],
        max_tokens=64,
    )
    print(f"  model    : {config.MODEL_NAME}")
    print(f"  reply    : {(reply.get('content') or '').strip()[:200]}")
    print(f"  usage    : {client.usage.as_dict()}")


def main() -> int:
    case_id = sys.argv[1] if len(sys.argv) > 1 else "EC_001"

    print("== data layer ==")
    store = get_store()
    print(f"  orders   : {len(store.orders):,}")
    print(f"  items    : {len(store.items):,}")
    print(f"  payments : {len(store.payments):,}")
    print(f"  customers: {len(store.customers):,}")
    print(f"  products : {len(store.products):,}")
    print(f"  sellers  : {len(store.sellers):,}")

    print(f"\n== case profile: {case_id} ==")
    profile_case(case_id)

    print(f"\n== llm connectivity ==")
    check_llm()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
