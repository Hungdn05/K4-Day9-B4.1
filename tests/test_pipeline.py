import unittest
from pathlib import Path

from ecommerce_dispute.config import POLICY_VERSION
from ecommerce_dispute.contracts import CaseRequest, validate_case_output
from ecommerce_dispute.pipeline import DisputeCoordinator
from ecommerce_dispute.repository import OlistRepository


def fixture_repository() -> OlistRepository:
    return OlistRepository.from_rows({
        "customers": [
            {"customer_id": "c-1", "customer_unique_id": "unique-1"},
            {"customer_id": "c-2", "customer_unique_id": "unique-1"},
        ],
        "orders": [
            {
                "order_id": "order-1", "customer_id": "c-1", "order_status": "delivered",
                "order_delivered_carrier_date": "2018-01-02 00:00:00",
                "order_delivered_customer_date": "2018-01-04 00:00:00",
                "order_estimated_delivery_date": "2018-01-03 00:00:00",
            },
            {
                "order_id": "order-history", "customer_id": "c-2", "order_status": "delivered",
                "order_delivered_carrier_date": "2017-01-02 00:00:00",
                "order_delivered_customer_date": "2017-01-03 00:00:00",
                "order_estimated_delivery_date": "2017-01-04 00:00:00",
            },
        ],
        "order_items": [
            {
                "order_id": "order-1", "order_item_id": "1", "product_id": "p-1", "seller_id": "seller-late",
                "shipping_limit_date": "2018-01-01 00:00:00", "price": "100.00", "freight_value": "10.00",
            },
            {
                "order_id": "order-1", "order_item_id": "2", "product_id": "p-2", "seller_id": "seller-on-time",
                "shipping_limit_date": "2018-01-03 00:00:00", "price": "20.00", "freight_value": "5.00",
            },
        ],
        "order_payments": [
            {"order_id": "order-1", "payment_sequential": "1", "payment_type": "credit_card", "payment_value": "115.00"},
            {"order_id": "order-1", "payment_sequential": "2", "payment_type": "voucher", "payment_value": "20.00"},
        ],
        "products": [
            {"product_id": "p-1", "product_category_name": "cat-a"},
            {"product_id": "p-2", "product_category_name": "cat-b"},
        ],
    })


class PipelineTests(unittest.TestCase):
    def test_pipeline_assembles_seller_late_case_with_auditable_handoffs(self) -> None:
        request = CaseRequest(
            case_id="EC_001", language="vi", message="Investigate", claimed_order_id="order-1",
            include_customer_history=True, include_product_context=True, policy_version=POLICY_VERSION,
        )
        output, handoffs = DisputeCoordinator(fixture_repository()).investigate(request)
        validate_case_output(output, "EC_001")
        self.assertEqual(output["case_assessment"]["primary_issue"], "late_delivery_seller")
        self.assertEqual(output["financial_resolution"]["recommended_refund_brl"], 15.0)
        self.assertEqual(output["delivery_analysis"]["delivery_variance_hours"], 24.0)
        self.assertEqual(output["delivery_analysis"]["late_handoff_seller_ids"], ["seller-late"])
        self.assertEqual(
            output["resolution_actions"],
            ["refund_freight", "review_seller_handoff", "verify_refund_completion", "coordinate_multi_seller_case", "verify_payment_allocation"],
        )
        self.assertEqual([handoff.agent.value for handoff in handoffs], [
            "customer", "order_product", "payment", "delivery", "policy", "verifier", "coordinator",
        ])
        self.assertIn("policy:SELLER_HANDOFF_AFTER_LIMIT", output["evidence_ids"])
        self.assertEqual(output["evidence_ids"][0], "order:order-1")

    def test_no_item_order_keeps_item_dependent_payment_fields_null(self) -> None:
        repository = OlistRepository.from_rows({
            "customers": [{"customer_id": "c", "customer_unique_id": "u"}],
            "orders": [{"order_id": "o", "customer_id": "c", "order_status": "canceled", "order_delivered_carrier_date": "", "order_delivered_customer_date": "", "order_estimated_delivery_date": ""}],
            "order_items": [],
            "order_payments": [{"order_id": "o", "payment_sequential": "1", "payment_type": "voucher", "payment_value": "12.34"}],
            "products": [],
        })
        request = CaseRequest("EC_002", "vi", "Investigate", "o", True, True, POLICY_VERSION)
        output, _ = DisputeCoordinator(repository).investigate(request)
        reconciliation = output["payment_reconciliation"]
        self.assertIsNone(reconciliation["expected_total_brl"])
        self.assertIsNone(reconciliation["difference_brl"])
        self.assertIsNone(reconciliation["reconciled"])
        # A sum over zero item rows is 0.00; only the three named fields are null.
        self.assertEqual(reconciliation["item_total_brl"], 0.0)
        self.assertEqual(reconciliation["freight_total_brl"], 0.0)
        self.assertEqual(output["financial_resolution"]["recommended_refund_brl"], 12.34)

    def test_complaint_text_can_never_become_a_fact(self) -> None:
        """The claim is not evidence: the same order must resolve the same way
        whether the customer asserts a late delivery or asks a neutral question,
        and a late claim contradicted by the timestamps must be rejected."""

        repository = OlistRepository.from_rows({
            "customers": [{"customer_id": "c", "customer_unique_id": "u"}],
            "orders": [{"order_id": "o", "customer_id": "c", "order_status": "delivered",
                        "order_delivered_carrier_date": "2018-01-02 09:00:00",
                        "order_delivered_customer_date": "2018-01-05 09:00:00",
                        "order_estimated_delivery_date": "2018-01-10 00:00:00"}],
            "order_items": [{"order_id": "o", "order_item_id": "1", "product_id": "p", "seller_id": "s",
                             "shipping_limit_date": "2018-01-04 09:00:00", "price": "10.00", "freight_value": "2.00"}],
            "order_payments": [{"order_id": "o", "payment_sequential": "1", "payment_type": "credit_card", "payment_value": "12.00"}],
            "products": [{"product_id": "p", "product_category_name": "cama_mesa_banho"}],
        })
        coordinator = DisputeCoordinator(repository)
        loud = CaseRequest("EC_001", "vi", "Hàng giao trễ 10 ngày, tôi yêu cầu hoàn lại toàn bộ tiền ngay.", "o", True, True, POLICY_VERSION)
        quiet = CaseRequest("EC_001", "vi", "Nhờ kiểm tra giúp đơn hàng này.", "o", True, True, POLICY_VERSION)
        from_claim, _ = coordinator.investigate(loud)
        from_question, _ = coordinator.investigate(quiet)
        self.assertEqual(from_claim, from_question)
        # Delivered five days inside the estimate, and the payment reconciles.
        self.assertEqual(from_claim["case_assessment"]["primary_issue"], "unsupported_late_claim")
        self.assertEqual(from_claim["case_assessment"]["case_status"], "no_action")
        self.assertEqual(from_claim["financial_resolution"]["recommended_refund_brl"], 0.0)
        self.assertEqual(from_claim["resolution_actions"], ["reject_late_refund"])
        self.assertEqual(from_claim["root_cause_analysis"]["responsible_parties"], [])

    def test_order_never_handed_to_carrier_has_no_handoff_analysis(self) -> None:
        """No carrier handoff event in the data means there is no handoff to analyse."""

        repository = OlistRepository.from_rows({
            "customers": [{"customer_id": "c", "customer_unique_id": "u"}],
            "orders": [{"order_id": "o", "customer_id": "c", "order_status": "canceled", "order_delivered_carrier_date": "", "order_delivered_customer_date": "", "order_estimated_delivery_date": "2017-08-04 00:00:00"}],
            "order_items": [{"order_id": "o", "order_item_id": "1", "product_id": "p", "seller_id": "s", "shipping_limit_date": "2017-07-20 10:00:00", "price": "10.00", "freight_value": "2.00"}],
            "order_payments": [{"order_id": "o", "payment_sequential": "1", "payment_type": "credit_card", "payment_value": "12.00"}],
            "products": [{"product_id": "p", "product_category_name": "cama_mesa_banho"}],
        })
        request = CaseRequest("EC_004", "vi", "Investigate", "o", True, True, POLICY_VERSION)
        output, _ = DisputeCoordinator(repository).investigate(request)
        self.assertEqual(output["delivery_analysis"]["seller_handoff_analysis"], [])
        self.assertEqual(output["delivery_analysis"]["late_handoff_seller_ids"], [])
        self.assertIsNone(output["delivery_analysis"]["delivery_variance_hours"])
        # The item itself is still an affected entity; only the handoff claim goes.
        self.assertEqual(output["affected_entities"]["item_ids"], ["o:1"])
        self.assertEqual(output["affected_entities"]["seller_ids"], ["s"])

    def test_real_olist_representatives_cover_every_primary_policy_branch(self) -> None:
        repository = OlistRepository.from_data_dir(Path(__file__).resolve().parents[1] / "data")
        coordinator = DisputeCoordinator(repository)
        representative_orders = {
            "e481f51cbdc54678b7cc49136f2d6af7": "valid_split_payment",
            "53cdb2fc8bc7dce0b6741e2150273451": "unsupported_late_claim",
            "203096f03d82e0dffbc41ebc2e2bcfb7": "late_delivery_seller",
            "fbf9ac61453ac646ce8ad9783d7d0af6": "late_delivery_logistics",
            "8e24261a7e58791d10cb1bf9da94df5c": "unavailable_order_paid",
            "1b9ecfe83cdc259250e1a8aca174f0ad": "canceled_order_paid",
        }
        for order_id, primary_issue in representative_orders.items():
            request = CaseRequest("QA", "vi", "qa", order_id, True, True, POLICY_VERSION)
            output, handoffs = coordinator.investigate(request)
            self.assertEqual(output["case_assessment"]["primary_issue"], primary_issue)
            self.assertEqual(len(handoffs), 7)
            validate_case_output(output, "QA")


if __name__ == "__main__":
    unittest.main()
