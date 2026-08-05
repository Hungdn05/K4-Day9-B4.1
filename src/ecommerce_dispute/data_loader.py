"""Read-only access and structural checks for the supplied Olist CSV files."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


DATASET_COLUMNS: dict[str, tuple[str, ...]] = {
    "customers": (
        "customer_id", "customer_unique_id", "customer_zip_code_prefix",
        "customer_city", "customer_state",
    ),
    "orders": (
        "order_id", "customer_id", "order_status", "order_purchase_timestamp",
        "order_approved_at", "order_delivered_carrier_date",
        "order_delivered_customer_date", "order_estimated_delivery_date",
    ),
    "order_items": (
        "order_id", "order_item_id", "product_id", "seller_id",
        "shipping_limit_date", "price", "freight_value",
    ),
    "order_payments": (
        "order_id", "payment_sequential", "payment_type",
        "payment_installments", "payment_value",
    ),
    "order_reviews": (
        "review_id", "order_id", "review_score", "review_comment_title",
        "review_comment_message", "review_creation_date", "review_answer_timestamp",
    ),
    "products": (
        "product_id", "product_category_name", "product_name_lenght",
        "product_description_lenght", "product_photos_qty", "product_weight_g",
        "product_length_cm", "product_height_cm", "product_width_cm",
    ),
    "sellers": ("seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"),
    "geolocation": (
        "geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng",
        "geolocation_city", "geolocation_state",
    ),
    "category_translation": ("product_category_name", "product_category_name_english"),
}

DATASET_FILES = {
    "customers": "olist_customers_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}


class DatasetContractError(ValueError):
    """Raised when a supplied CSV cannot satisfy the lab's data contract."""


@dataclass(frozen=True)
class DatasetReport:
    """Result of a fast structural preflight, without loading whole CSV files."""

    name: str
    path: Path
    column_count: int


class OlistDataLoader:
    """Expose stable dataset names and streaming row access.

    The full data set contains more than 1.5 million rows.  This class only
    validates headers at construction time and streams data on demand, so the
    foundation remains safe on ordinary local machines.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def path_for(self, dataset_name: str) -> Path:
        try:
            return self.data_dir / DATASET_FILES[dataset_name]
        except KeyError as exc:
            raise KeyError(f"Unknown dataset: {dataset_name}") from exc

    def preflight(self) -> list[DatasetReport]:
        reports: list[DatasetReport] = []
        for name, required_columns in DATASET_COLUMNS.items():
            path = self.path_for(name)
            if not path.is_file():
                raise DatasetContractError(f"Missing required dataset: {path}")
            with path.open("r", encoding="utf-8-sig", newline="") as source:
                header = next(csv.reader(source), None)
            if header is None:
                raise DatasetContractError(f"Dataset has no header: {path}")
            if tuple(header) != required_columns:
                raise DatasetContractError(
                    f"Unexpected columns in {path.name}: {header!r}; "
                    f"expected {list(required_columns)!r}"
                )
            reports.append(DatasetReport(name, path, len(header)))
        return reports

    def rows(self, dataset_name: str) -> Iterator[dict[str, str]]:
        """Yield CSV rows preserving their source order."""

        path = self.path_for(dataset_name)
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            yield from csv.DictReader(source)
