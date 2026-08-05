"""Customer-domain tools: identity resolution and purchase history."""

from __future__ import annotations

from src.data.store import get_store
from src.tools.registry import ORDER_ID_PARAM, ToolError, make_toolbox


def lookup_customer_identity(order_id: str) -> dict:
    """Resolve the shopper behind an order."""
    store = get_store()
    order = store.get_order(order_id)
    if order is None:
        raise ToolError(f"order_id {order_id!r} does not exist in olist_orders_dataset.csv")

    customer = store.get_customer(order["customer_id"])
    if customer is None:
        raise ToolError(f"customer_id {order['customer_id']!r} missing from customers CSV")

    return {
        "order_id": order_id,
        "customer_id": order["customer_id"],
        "customer_unique_id": customer["customer_unique_id"],
        "customer_city": customer.get("customer_city"),
        "customer_state": customer.get("customer_state"),
    }


def list_customer_order_history(customer_unique_id: str, exclude_order_id: str = "") -> dict:
    """List every other order placed by the same shopper, oldest purchase first."""
    store = get_store()
    history = store.get_orders_for_customer_unique(customer_unique_id)
    if not history:
        raise ToolError(
            f"customer_unique_id {customer_unique_id!r} has no orders; "
            "call lookup_customer_identity first to get a valid id"
        )

    related = [
        {
            "order_id": row["order_id"],
            "order_status": row["order_status"],
            "order_purchase_timestamp": row["order_purchase_timestamp"],
        }
        for row in history
        if row["order_id"] != exclude_order_id
    ]

    return {
        "customer_unique_id": customer_unique_id,
        "total_orders_by_shopper": len(history),
        "related_order_ids": [row["order_id"] for row in related],
        "related_orders": related,
        "is_repeat_customer": len(related) > 0,
    }


CUSTOMER_TOOLS = make_toolbox(
    "customer",
    [
        (
            lookup_customer_identity,
            "Resolve customer_id and customer_unique_id for an order. Always call this first.",
            ORDER_ID_PARAM,
        ),
        (
            list_customer_order_history,
            "List all other orders belonging to the same customer_unique_id. "
            "Use the customer_unique_id returned by lookup_customer_identity, and pass the "
            "order under investigation as exclude_order_id so it is left out of the history.",
            {
                "type": "object",
                "properties": {
                    "customer_unique_id": {
                        "type": "string",
                        "description": "customer_unique_id from lookup_customer_identity",
                    },
                    "exclude_order_id": {
                        "type": "string",
                        "description": "The order under investigation, excluded from the history",
                    },
                },
                "required": ["customer_unique_id"],
                "additionalProperties": False,
            },
        ),
    ],
)
