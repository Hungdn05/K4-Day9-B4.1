"""In-memory Olist store.

Loads once per process and exposes pre-built indexes. Timestamps are kept as the
raw CSV strings (`YYYY-MM-DD HH:MM:SS`) because the output schema must echo them
verbatim; parsing happens only inside the variance calculators.

Deliberately does not load `olist_geolocation_dataset.csv` (1M rows) or
`olist_order_reviews_dataset.csv`: no EC_POLICY_V2 rule reads them, and skipping
them keeps the footprint under 200 MB.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import pandas as pd

from src import config

_TS_COLS = (
    "order_purchase_timestamp",
    "order_approved_at",
    "order_delivered_carrier_date",
    "order_delivered_customer_date",
    "order_estimated_delivery_date",
)


def _read(name: str) -> pd.DataFrame:
    return pd.read_csv(config.DATA_DIR / name, dtype=str, keep_default_na=True)


@dataclass
class OlistStore:
    orders: pd.DataFrame
    items: pd.DataFrame
    payments: pd.DataFrame
    customers: pd.DataFrame
    products: pd.DataFrame
    sellers: pd.DataFrame
    categories: pd.DataFrame

    # --- indexes -------------------------------------------------------------
    _orders_by_id: dict[str, dict]
    _items_by_order: dict[str, list[dict]]
    _payments_by_order: dict[str, list[dict]]
    _customer_by_id: dict[str, dict]
    _orders_by_customer_unique: dict[str, list[dict]]
    _product_by_id: dict[str, dict]
    _category_en: dict[str, str]

    # --- order ---------------------------------------------------------------
    def get_order(self, order_id: str) -> dict | None:
        return self._orders_by_id.get(order_id)

    def get_items(self, order_id: str) -> list[dict]:
        """Item rows for an order, ordered by `order_item_id` ascending."""
        return self._items_by_order.get(order_id, [])

    def get_payments(self, order_id: str) -> list[dict]:
        """Payment rows for an order, ordered by `payment_sequential` ascending."""
        return self._payments_by_order.get(order_id, [])

    # --- customer ------------------------------------------------------------
    def get_customer(self, customer_id: str) -> dict | None:
        return self._customer_by_id.get(customer_id)

    def get_orders_for_customer_unique(self, customer_unique_id: str) -> list[dict]:
        """All orders of the same shopper, oldest purchase first."""
        return self._orders_by_customer_unique.get(customer_unique_id, [])

    # --- product -------------------------------------------------------------
    def get_product(self, product_id: str) -> dict | None:
        return self._product_by_id.get(product_id)

    def category_english(self, category_pt: str | None) -> str | None:
        if not category_pt:
            return None
        return self._category_en.get(category_pt, category_pt)


def _records(df: pd.DataFrame) -> list[dict]:
    return df.where(pd.notna(df), None).to_dict("records")


@lru_cache(maxsize=1)
def get_store() -> OlistStore:
    orders = _read("olist_orders_dataset.csv")
    items = _read("olist_order_items_dataset.csv")
    payments = _read("olist_order_payments_dataset.csv")
    customers = _read("olist_customers_dataset.csv")
    products = _read("olist_products_dataset.csv")
    sellers = _read("olist_sellers_dataset.csv")
    categories = _read("product_category_name_translation.csv")

    # Stable ordering so every array in the output is reproducible.
    items = items.assign(_seq=items["order_item_id"].astype(int)).sort_values(
        ["order_id", "_seq"], kind="mergesort"
    )
    payments = payments.assign(_seq=payments["payment_sequential"].astype(int)).sort_values(
        ["order_id", "_seq"], kind="mergesort"
    )

    orders_by_id: dict[str, dict] = {}
    for row in _records(orders):
        orders_by_id[row["order_id"]] = row

    items_by_order: dict[str, list[dict]] = {}
    for row in _records(items.drop(columns="_seq")):
        items_by_order.setdefault(row["order_id"], []).append(row)

    payments_by_order: dict[str, list[dict]] = {}
    for row in _records(payments.drop(columns="_seq")):
        payments_by_order.setdefault(row["order_id"], []).append(row)

    customer_by_id: dict[str, dict] = {}
    unique_to_customer_ids: dict[str, list[str]] = {}
    for row in _records(customers):
        customer_by_id[row["customer_id"]] = row
        unique_to_customer_ids.setdefault(row["customer_unique_id"], []).append(
            row["customer_id"]
        )

    orders_by_customer: dict[str, list[dict]] = {}
    for row in orders_by_id.values():
        orders_by_customer.setdefault(row["customer_id"], []).append(row)

    orders_by_unique: dict[str, list[dict]] = {}
    for unique_id, customer_ids in unique_to_customer_ids.items():
        bucket: list[dict] = []
        for cid in customer_ids:
            bucket.extend(orders_by_customer.get(cid, []))
        bucket.sort(key=lambda r: (r.get("order_purchase_timestamp") or "", r["order_id"]))
        orders_by_unique[unique_id] = bucket

    product_by_id = {row["product_id"]: row for row in _records(products)}
    category_en = {
        row["product_category_name"]: row["product_category_name_english"]
        for row in _records(categories)
    }

    return OlistStore(
        orders=orders,
        items=items,
        payments=payments,
        customers=customers,
        products=products,
        sellers=sellers,
        categories=categories,
        _orders_by_id=orders_by_id,
        _items_by_order=items_by_order,
        _payments_by_order=payments_by_order,
        _customer_by_id=customer_by_id,
        _orders_by_customer_unique=orders_by_unique,
        _product_by_id=product_by_id,
        _category_en=category_en,
    )
