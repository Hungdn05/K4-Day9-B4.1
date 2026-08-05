"""Delivery-domain tools: delivery variance and per-seller handoff variance."""

from __future__ import annotations

from src.data.store import get_store
from src.tools.calc import clean_ts, hours_between, parse_ts
from src.tools.registry import ORDER_ID_PARAM, ToolError, make_toolbox


def _require_order(order_id: str) -> dict:
    order = get_store().get_order(order_id)
    if order is None:
        raise ToolError(f"order_id {order_id!r} does not exist in olist_orders_dataset.csv")
    return order


def get_delivery_timeline(order_id: str) -> dict:
    """Delivery timestamps plus delivery_variance_hours (positive means late)."""
    order = _require_order(order_id)

    delivered = clean_ts(order["order_delivered_customer_date"])
    estimated = clean_ts(order["order_estimated_delivery_date"])
    handoff = clean_ts(order["order_delivered_carrier_date"])
    variance = hours_between(delivered, estimated)

    return {
        "order_id": order_id,
        "order_status": order["order_status"],
        "delivered_at": delivered,
        "estimated_delivery_at": estimated,
        "carrier_handoff_at": handoff,
        "delivery_variance_hours": variance,
        "delivered_late": bool(variance is not None and variance > 0),
        "note": (
            "Order was never delivered to the customer, so delivery_variance_hours is null "
            "and no late-delivery rule can apply."
            if delivered is None
            else ""
        ),
    }


def analyze_seller_handoff(order_id: str) -> dict:
    """Per-seller handoff analysis against each seller's earliest shipping_limit_date.

    handoff_variance_hours = order_delivered_carrier_date - earliest shipping_limit_date
    of that seller. Positive means the carrier picked the goods up after the seller's
    deadline, i.e. the seller handed off late.
    """
    order = _require_order(order_id)
    items = get_store().get_items(order_id)

    if not items:
        return {
            "order_id": order_id,
            "carrier_handoff_at": clean_ts(order["order_delivered_carrier_date"]),
            "seller_handoff_analysis": [],
            "late_handoff_seller_ids": [],
            "any_late_handoff": False,
            "note": "Order has no item rows, so there is no seller handoff to analyse.",
        }

    handoff = clean_ts(order["order_delivered_carrier_date"])

    earliest_limit: dict[str, str] = {}
    seller_order: list[str] = []
    for row in items:
        seller = row["seller_id"]
        limit = clean_ts(row["shipping_limit_date"])
        if seller not in earliest_limit:
            seller_order.append(seller)
            earliest_limit[seller] = limit
        elif limit and (
            earliest_limit[seller] is None or parse_ts(limit) < parse_ts(earliest_limit[seller])
        ):
            earliest_limit[seller] = limit

    analysis = []
    late_sellers = []
    for seller in seller_order:
        limit = earliest_limit[seller]
        variance = hours_between(handoff, limit)
        is_late = bool(variance is not None and variance > 0)
        if is_late:
            late_sellers.append(seller)
        analysis.append(
            {
                "seller_id": seller,
                "shipping_limit_at": limit,
                "handoff_variance_hours": variance,
                "late_handoff": is_late,
            }
        )

    return {
        "order_id": order_id,
        "carrier_handoff_at": handoff,
        "seller_handoff_analysis": analysis,
        "late_handoff_seller_ids": late_sellers,
        "any_late_handoff": bool(late_sellers),
        "note": (
            "Carrier never picked the order up, so every handoff_variance_hours is null "
            "and no seller can be shown to have handed off late."
            if handoff is None
            else ""
        ),
    }


DELIVERY_TOOLS = make_toolbox(
    "delivery",
    [
        (
            get_delivery_timeline,
            "Delivery timestamps (delivered_at, estimated_delivery_at, carrier_handoff_at) "
            "and delivery_variance_hours. Positive variance means delivered after the "
            "estimated date; null means the order was never delivered.",
            ORDER_ID_PARAM,
        ),
        (
            analyze_seller_handoff,
            "Per-seller handoff analysis: each seller's earliest shipping_limit_date, the "
            "handoff_variance_hours against carrier pickup, and which sellers handed off "
            "late. This decides late_delivery_seller versus late_delivery_logistics.",
            ORDER_ID_PARAM,
        ),
    ],
)
