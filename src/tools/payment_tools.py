"""Payment-domain tools: payment rows and reconciliation against item + freight."""

from __future__ import annotations

from decimal import Decimal

from src import config
from src.data.store import get_store
from src.tools.calc import dec_sum, round2
from src.tools.registry import ORDER_ID_PARAM, ToolError, make_toolbox


def _require_order(order_id: str) -> None:
    if get_store().get_order(order_id) is None:
        raise ToolError(f"order_id {order_id!r} does not exist in olist_orders_dataset.csv")


def list_order_payments(order_id: str) -> dict:
    """List every payment row of the order, ordered by payment_sequential."""
    _require_order(order_id)
    payments = get_store().get_payments(order_id)

    rows = [
        {
            "payment_id": f"{order_id}:{row['payment_sequential']}",
            "payment_sequential": row["payment_sequential"],
            "payment_type": row["payment_type"],
            "payment_installments": row["payment_installments"],
            "payment_value": round2(row["payment_value"]),
        }
        for row in payments
    ]

    types: list[str] = []
    for row in payments:
        if row["payment_type"] not in types:
            types.append(row["payment_type"])

    return {
        "order_id": order_id,
        "payment_row_count": len(rows),
        "payments": rows,
        "payment_ids": [row["payment_id"] for row in rows],
        "payment_types": types,
        "payment_total_brl": round2(dec_sum(r["payment_value"] for r in payments)),
        "is_split_payment": len(rows) >= 2,
        "note": (
            "payment_value is the amount of one payment row, not one installment."
        ),
    }


def reconcile_order_payment(order_id: str) -> dict:
    """Reconcile total payments against item price + freight.

    Returns expected_total_brl, difference_brl and reconciled as null when the order
    has no item rows -- there is nothing to reconcile against.

    item_total_brl and freight_total_brl stay real numbers even then, because summing
    zero item rows is 0.00 rather than unknown. README section 4 names only the other
    three fields as null, and an earlier version wrongly nulled these two as well.
    """
    _require_order(order_id)
    store = get_store()
    items = store.get_items(order_id)
    payments = store.get_payments(order_id)

    payment_total = dec_sum(r["payment_value"] for r in payments)

    if not items:
        return {
            "order_id": order_id,
            "currency": config.CURRENCY,
            "item_total_brl": 0.0,
            "freight_total_brl": 0.0,
            "expected_total_brl": None,
            "payment_total_brl": round2(payment_total),
            "difference_brl": None,
            "reconciled": None,
            "payment_types": [r["payment_type"] for r in payments],
            "note": "Order has no item rows, so expected/difference/reconciled are null.",
        }

    item_total = dec_sum(r["price"] for r in items)
    freight_total = dec_sum(r["freight_value"] for r in items)
    expected = item_total + freight_total
    difference = payment_total - expected
    tolerance = Decimal(str(config.RECONCILE_TOLERANCE_BRL))

    types: list[str] = []
    for row in payments:
        if row["payment_type"] not in types:
            types.append(row["payment_type"])

    return {
        "order_id": order_id,
        "currency": config.CURRENCY,
        "item_total_brl": round2(item_total),
        "freight_total_brl": round2(freight_total),
        "expected_total_brl": round2(expected),
        "payment_total_brl": round2(payment_total),
        "difference_brl": round2(difference),
        "reconciled": abs(difference) <= tolerance,
        "tolerance_brl": config.RECONCILE_TOLERANCE_BRL,
        "payment_types": types,
        "payment_row_count": len(payments),
        "is_split_payment": len(payments) >= 2,
    }


PAYMENT_TOOLS = make_toolbox(
    "payment",
    [
        (
            list_order_payments,
            "All payment rows for the order with sequential, type, installments and value.",
            ORDER_ID_PARAM,
        ),
        (
            reconcile_order_payment,
            "Reconcile total payment against item price + freight: returns item_total_brl, "
            "freight_total_brl, expected_total_brl, payment_total_brl, difference_brl and "
            "reconciled (|difference| <= 0.10 BRL). All three expected/difference/reconciled "
            "come back null when the order has no item rows.",
            ORDER_ID_PARAM,
        ),
    ],
)
