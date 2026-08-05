"""Order/item/seller/product-domain tools."""

from __future__ import annotations

from src import config
from src.data.store import get_store
from src.tools.calc import clean_ts, dec_sum, round2
from src.tools.registry import ORDER_ID_PARAM, ToolError, make_toolbox


def _require_order(order_id: str) -> dict:
    order = get_store().get_order(order_id)
    if order is None:
        raise ToolError(f"order_id {order_id!r} does not exist in olist_orders_dataset.csv")
    return order


def get_order_core(order_id: str) -> dict:
    """Fetch the order header: status, purchase time and how many item rows exist."""
    order = _require_order(order_id)
    items = get_store().get_items(order_id)
    return {
        "order_id": order_id,
        "customer_id": order["customer_id"],
        "order_status": order["order_status"],
        "order_purchase_timestamp": clean_ts(order["order_purchase_timestamp"]),
        "order_approved_at": clean_ts(order["order_approved_at"]),
        "item_row_count": len(items),
        "has_item_rows": bool(items),
        "note": (
            "This order has no item rows: item/seller/product/category arrays must stay "
            "empty and expected_total_brl, difference_brl, reconciled must be null."
            if not items
            else ""
        ),
    }


def list_order_items(order_id: str) -> dict:
    """List every item row of the order in source order, with seller and money columns."""
    _require_order(order_id)
    items = get_store().get_items(order_id)

    rows = [
        {
            "item_id": f"{order_id}:{row['order_item_id']}",
            "order_item_id": row["order_item_id"],
            "product_id": row["product_id"],
            "seller_id": row["seller_id"],
            "shipping_limit_date": clean_ts(row["shipping_limit_date"]),
            "price": round2(row["price"]),
            "freight_value": round2(row["freight_value"]),
        }
        for row in items
    ]

    seller_ids: list[str] = []
    for row in items:
        if row["seller_id"] not in seller_ids:
            seller_ids.append(row["seller_id"])

    return {
        "order_id": order_id,
        "item_count": len(rows),
        "items": rows,
        "item_ids": [row["item_id"] for row in rows],
        "seller_ids": seller_ids,
        "distinct_seller_count": len(seller_ids),
        "item_total_brl": round2(dec_sum(r["price"] for r in items)) if items else None,
        "freight_total_brl": round2(dec_sum(r["freight_value"] for r in items)) if items else None,
        "is_multi_item_order": len(rows) >= 2,
        "is_multi_seller_order": len(seller_ids) >= 2,
    }


def get_order_product_context(order_id: str) -> dict:
    """Resolve product ids and category names for the order, in item-row order."""
    _require_order(order_id)
    store = get_store()
    items = store.get_items(order_id)

    product_ids: list[str] = []
    categories_pt: list[str] = []
    categories_en: list[str] = []
    unresolved: list[str] = []

    for row in items:
        pid = row["product_id"]
        if pid not in product_ids:
            product_ids.append(pid)
        product = store.get_product(pid)
        if product is None:
            unresolved.append(pid)
            continue
        cat_pt = product.get("product_category_name")
        if not cat_pt:
            continue
        if cat_pt not in categories_pt:
            categories_pt.append(cat_pt)
            categories_en.append(store.category_english(cat_pt))

    preferred = categories_pt if config.CATEGORY_LANGUAGE == "pt" else categories_en

    return {
        "order_id": order_id,
        "product_ids": product_ids,
        "category_names": preferred,
        "category_names_pt": categories_pt,
        "category_names_en": categories_en,
        "distinct_category_count": len(categories_pt),
        "has_multiple_categories": len(categories_pt) >= 2,
        "products_missing_from_catalog": unresolved,
    }


ORDER_TOOLS = make_toolbox(
    "order_product",
    [
        (
            get_order_core,
            "Order header: order_status, purchase/approval timestamps and item row count. "
            "Call this first; order_status drives the top of the policy table.",
            ORDER_ID_PARAM,
        ),
        (
            list_order_items,
            "All item rows with product_id, seller_id, shipping_limit_date, price and "
            "freight_value, plus distinct seller ids and item/freight totals.",
            ORDER_ID_PARAM,
        ),
        (
            get_order_product_context,
            "Distinct product ids and category names for the order, in item-row order.",
            ORDER_ID_PARAM,
        ),
    ],
)
