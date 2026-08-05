"""Indexed, read-only Olist records for the domain agents."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .data_loader import OlistDataLoader


class RecordNotFoundError(LookupError):
    """Raised when a claimed ID has no corresponding Olist source record."""


def _freeze_grouped(rows: list[dict[str, str]], key: str) -> dict[str, tuple[dict[str, str], ...]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row[key]].append(row)
    return {group_key: tuple(group_rows) for group_key, group_rows in grouped.items()}


@dataclass(frozen=True)
class OlistRepository:
    """Indexes only datasets required by the CP3 investigation pipeline."""

    customers_by_id: dict[str, dict[str, str]]
    orders_by_id: dict[str, dict[str, str]]
    orders_by_customer_unique_id: dict[str, tuple[dict[str, str], ...]]
    items_by_order_id: dict[str, tuple[dict[str, str], ...]]
    payments_by_order_id: dict[str, tuple[dict[str, str], ...]]
    products_by_id: dict[str, dict[str, str]]

    @classmethod
    def from_data_dir(cls, data_dir: Path) -> "OlistRepository":
        loader = OlistDataLoader(data_dir)
        loader.preflight()
        rows = {name: list(loader.rows(name)) for name in (
            "customers", "orders", "order_items", "order_payments", "products",
        )}
        return cls.from_rows(rows)

    @classmethod
    def from_rows(cls, rows: dict[str, list[dict[str, str]]]) -> "OlistRepository":
        """Build a repository from rows; intended for deterministic tests too."""

        required = {"customers", "orders", "order_items", "order_payments", "products"}
        missing = required - set(rows)
        if missing:
            raise ValueError(f"Missing repository datasets: {sorted(missing)}")
        customers_by_id = {row["customer_id"]: row for row in rows["customers"]}
        orders_by_id = {row["order_id"]: row for row in rows["orders"]}
        orders_with_identity: list[dict[str, str]] = []
        for order in rows["orders"]:
            customer = customers_by_id.get(order["customer_id"])
            if customer is None:
                raise RecordNotFoundError(f"Order {order['order_id']} has unknown customer")
            orders_with_identity.append({**order, "customer_unique_id": customer["customer_unique_id"]})
        return cls(
            customers_by_id=customers_by_id,
            orders_by_id=orders_by_id,
            orders_by_customer_unique_id=_freeze_grouped(orders_with_identity, "customer_unique_id"),
            items_by_order_id=_freeze_grouped(rows["order_items"], "order_id"),
            payments_by_order_id=_freeze_grouped(rows["order_payments"], "order_id"),
            products_by_id={row["product_id"]: row for row in rows["products"]},
        )

    def order(self, order_id: str) -> dict[str, str]:
        try:
            return self.orders_by_id[order_id]
        except KeyError as exc:
            raise RecordNotFoundError(f"Claimed order does not exist: {order_id}") from exc

    def customer_for_order(self, order_id: str) -> dict[str, str]:
        order = self.order(order_id)
        try:
            return self.customers_by_id[order["customer_id"]]
        except KeyError as exc:
            raise RecordNotFoundError(f"Order {order_id} has no customer record") from exc

    def product(self, product_id: str) -> dict[str, str]:
        try:
            return self.products_by_id[product_id]
        except KeyError as exc:
            raise RecordNotFoundError(f"Item references missing product: {product_id}") from exc
