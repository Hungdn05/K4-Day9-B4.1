import json
import tempfile
import unittest
from pathlib import Path

from ecommerce_dispute.config import MODEL_CONFIG, POLICY_VERSION
from ecommerce_dispute.contracts import CaseContractError, read_case_request
from ecommerce_dispute.data_loader import OlistDataLoader
from ecommerce_dispute.policy import CaseFacts, PolicyResolutionError, decide_policy
from decimal import Decimal


class FoundationTests(unittest.TestCase):
    def test_model_respects_lab_limit(self) -> None:
        self.assertLessEqual(
            MODEL_CONFIG["parameter_size_billion"], MODEL_CONFIG["max_parameter_size_billion"]
        )

    def test_supplied_data_matches_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(len(OlistDataLoader(root / "data").preflight()), 9)

    def test_case_request_contract(self) -> None:
        document = {
            "case_id": "EC_001",
            "customer_request": {"language": "vi", "message": "Investigate", "claimed_order_id": "order-1"},
            "investigation_scope": {"include_customer_history": True, "include_product_context": True},
            "policy_version": POLICY_VERSION,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "EC_001.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            self.assertEqual(read_case_request(path).case_id, "EC_001")
            document["policy_version"] = "wrong"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(CaseContractError):
                read_case_request(path)

    def test_policy_priority_and_actions(self) -> None:
        facts = CaseFacts(
            order_status="canceled", payment_total_brl=Decimal("33.21"),
            freight_total_brl=Decimal("8.50"), item_count=2, seller_ids=("s-1", "s-2"),
            payment_count=2, reconciled=True, delivered_late=True,
            late_handoff_seller_ids=("s-1",), is_repeat_customer=True, category_count=2,
        )
        decision = decide_policy(facts)
        self.assertEqual(decision.primary_issue, "canceled_order_paid")
        self.assertEqual(decision.recommended_refund_brl, Decimal("33.21"))
        self.assertEqual(
            decision.secondary_issues,
            ("multi_item_order", "multi_seller_order", "split_payment", "repeat_customer", "multiple_categories"),
        )
        self.assertEqual(
            decision.resolution_actions,
            ("issue_full_refund", "verify_refund_completion", "coordinate_multi_seller_case", "verify_payment_allocation"),
        )

    def test_policy_logistics_and_unprovable_case(self) -> None:
        late = CaseFacts(
            order_status="delivered", payment_total_brl=Decimal("20"), freight_total_brl=Decimal("3"),
            item_count=1, seller_ids=("s-1",), payment_count=1, reconciled=True,
            delivered_late=True, late_handoff_seller_ids=(), is_repeat_customer=False, category_count=1,
        )
        decision = decide_policy(late)
        self.assertEqual(decision.primary_issue, "late_delivery_logistics")
        self.assertEqual(decision.resolution_actions, ("refund_freight", "review_carrier_delay", "verify_refund_completion"))
        with self.assertRaises(PolicyResolutionError):
            decide_policy(CaseFacts(
                order_status="processing", payment_total_brl=Decimal("0"), freight_total_brl=None,
                item_count=0, seller_ids=(), payment_count=0, reconciled=None,
                delivered_late=None, late_handoff_seller_ids=(), is_repeat_customer=False, category_count=0,
            ))


if __name__ == "__main__":
    unittest.main()
