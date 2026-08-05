import json
from pathlib import Path
import unittest

from qa.internal_grader import IndependentOracle, OracleOptions


class InternalGraderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.oracle = IndependentOracle(cls.root / "data", OracleOptions())

    def test_no_item_order_nulls_exactly_the_three_named_fields(self) -> None:
        """EC_POLICY_V2 names three null fields; the two sums stay numeric."""

        case = json.loads((self.root / "input" / "EC_012.json").read_text(encoding="utf-8"))
        expected = self.oracle.expected(case)["payment_reconciliation"]
        self.assertEqual(expected["item_total_brl"], 0.0)
        self.assertEqual(expected["freight_total_brl"], 0.0)
        self.assertIsNone(expected["expected_total_brl"])
        self.assertIsNone(expected["difference_brl"])
        self.assertIsNone(expected["reconciled"])

    def test_readme_baseline_preserves_grounded_source_semantics(self) -> None:
        options = OracleOptions()
        self.assertTrue(options.no_item_totals_zero)
        self.assertFalse(options.translated_categories)
        self.assertFalse(options.responsible_sellers_only)
        self.assertFalse(options.no_action_primary_action_only)
        self.assertFalse(options.sort_payments_by_sequence)
        self.assertTrue(options.deduplicate_payment_types)

        case = json.loads((self.root / "input" / "EC_010.json").read_text(encoding="utf-8"))
        expected = self.oracle.expected(case)
        self.assertEqual(expected["product_context"]["category_names"], ["relogios_presentes"])
        self.assertEqual(len(expected["affected_entities"]["seller_ids"]), 2)
        self.assertEqual(expected["affected_entities"]["payment_ids"], [
            "919baca007d9525b6668c18f79a33197:2",
            "919baca007d9525b6668c18f79a33197:1",
        ])
        self.assertEqual(expected["payment_reconciliation"]["payment_types"], ["credit_card"])
        self.assertEqual(expected["resolution_actions"], [
            "explain_valid_split_payment", "coordinate_multi_seller_case",
        ])


if __name__ == "__main__":
    unittest.main()
