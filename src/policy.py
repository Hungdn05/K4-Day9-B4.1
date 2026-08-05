"""EC_POLICY_V2 taxonomy constants.

Single source of truth shared by the Policy Agent prompt (CP4) and the Verifier, so
the vocabulary the model is told to use is literally the vocabulary being validated.

This module holds names and orderings only. It deliberately contains no
`if status == "canceled"` decision code: choosing the primary issue is the Policy
Agent's job, and the Verifier only checks the choice for internal consistency.
"""

from __future__ import annotations

from src import config

PRIMARY_ISSUES = (
    "canceled_order_paid",
    "unavailable_order_paid",
    "late_delivery_seller",
    "late_delivery_logistics",
    "valid_split_payment",
    "unsupported_late_claim",
)

# README section 4: secondary issues must appear in exactly this order.
SECONDARY_ISSUE_ORDER = (
    "multi_item_order",
    "multi_seller_order",
    "split_payment",
    "repeat_customer",
    "multiple_categories",
)

ROOT_CAUSE_CODES = (
    "SELLER_HANDOFF_AFTER_LIMIT",
    "CARRIER_DELIVERED_AFTER_ESTIMATE",
    "ORDER_CANCELED_AFTER_PAYMENT",
    "ORDER_UNAVAILABLE_AFTER_PAYMENT",
    "MULTIPLE_PAYMENTS_RECONCILED",
    "DELIVERY_WITHIN_ESTIMATE",
)

PRIMARY_ACTIONS = {
    "canceled_order_paid": "issue_full_refund",
    "unavailable_order_paid": "issue_full_refund",
    "late_delivery_seller": "refund_freight",
    "late_delivery_logistics": "refund_freight",
    "valid_split_payment": "explain_valid_split_payment",
    "unsupported_late_claim": "reject_late_refund",
}

# README section 4: supplementary actions follow the primary action in this order.
SUPPLEMENTARY_ACTION_ORDER = (
    "review_seller_handoff",
    "review_carrier_delay",
    "verify_refund_completion",
    "coordinate_multi_seller_case",
    "verify_payment_allocation",
)

ALL_ACTIONS = tuple(dict.fromkeys(PRIMARY_ACTIONS.values())) + SUPPLEMENTARY_ACTION_ORDER

REFUND_BASIS = {
    "canceled_order_paid": "payment_total",
    "unavailable_order_paid": "payment_total",
    "late_delivery_seller": "freight_total",
    "late_delivery_logistics": "freight_total",
    "valid_split_payment": "none",
    "unsupported_late_claim": "none",
}

PARTY_TYPES = ("platform", "seller", "logistics_provider")
PLATFORM_PARTY_ID = "OLIST_PLATFORM"
LOGISTICS_PARTY_ID = "LOGISTICS_PROVIDER"

CASE_STATUSES = ("action_required", "no_action")

# The two no-fault rows. See config.NO_FAULT_RESPONSIBLE_PARTY for why this is not
# simply "nobody".
NO_FAULT_ISSUES = ("valid_split_payment", "unsupported_late_claim")
NO_FAULT_PARTY_TYPE = config.NO_FAULT_RESPONSIBLE_PARTY
_NO_FAULT_LINE = (
    f"responsible: {NO_FAULT_PARTY_TYPE} / {PLATFORM_PARTY_ID}"
    if NO_FAULT_PARTY_TYPE == "platform"
    else "responsible: nobody"
)

POLICY_TABLE_TEXT = f"""\
EC_POLICY_V2 primary issue table. Evaluate the rows STRICTLY TOP TO BOTTOM and stop at
the FIRST row whose condition holds. A lower row can never override a higher one.

1. canceled_order_paid
   IF order_status == "canceled" AND payment_total_brl > 0
   -> responsible: platform / OLIST_PLATFORM
   -> refund: payment_total_brl        -> action: issue_full_refund
   -> root cause: ORDER_CANCELED_AFTER_PAYMENT

2. unavailable_order_paid
   IF order_status == "unavailable" AND payment_total_brl > 0
   -> responsible: platform / OLIST_PLATFORM
   -> refund: payment_total_brl        -> action: issue_full_refund
   -> root cause: ORDER_UNAVAILABLE_AFTER_PAYMENT

3. late_delivery_seller
   IF delivery_variance_hours > 0 AND at least one seller has late_handoff == true
   -> responsible: seller / every late-handoff seller_id
   -> refund: freight_total_brl        -> action: refund_freight
   -> root cause: SELLER_HANDOFF_AFTER_LIMIT

4. late_delivery_logistics
   IF delivery_variance_hours > 0 AND no seller handed off late
   -> responsible: logistics_provider / LOGISTICS_PROVIDER
   -> refund: freight_total_brl        -> action: refund_freight
   -> root cause: CARRIER_DELIVERED_AFTER_ESTIMATE

5. valid_split_payment
   IF payment_row_count >= 2 AND reconciled == true
   -> {_NO_FAULT_LINE}
   -> refund: 0                        -> action: explain_valid_split_payment
   -> root cause: MULTIPLE_PAYMENTS_RECONCILED

6. unsupported_late_claim
   IF the order was delivered no later than the estimated date AND payments reconcile
   -> {_NO_FAULT_LINE}
   -> refund: 0                        -> action: reject_late_refund
   -> root cause: DELIVERY_WITHIN_ESTIMATE

Note on rows 5 and 6: no seller and no carrier did anything wrong, but the case still
belongs to someone -- the platform owns explaining the outcome to the customer. Report
responsible_party_type = "{NO_FAULT_PARTY_TYPE}" for these two rows.
"""

SECONDARY_RULES_TEXT = """\
Secondary issues are ADDITIVE and must be emitted in exactly this order, keeping only
the ones whose condition holds:

1. multi_item_order      -- the order has 2 or more item rows
2. multi_seller_order    -- the order has 2 or more distinct seller_id values
3. split_payment         -- the order has 2 or more payment rows
4. repeat_customer       -- the same customer_unique_id has at least one other order
5. multiple_categories   -- the order spans 2 or more distinct product categories

A condition that already decided the primary issue is still reported as a secondary
issue when it holds. Example: a canceled order with 3 payment rows is
primary=canceled_order_paid AND secondary includes split_payment.
"""

ACTION_RULES_TEXT = """\
resolution_actions starts with the primary action from the table above, then appends
any applicable supplementary actions in this fixed order:

1. review_seller_handoff       -- when primary is late_delivery_seller
   review_carrier_delay        -- when primary is late_delivery_logistics
2. verify_refund_completion    -- ONLY when the primary action is issue_full_refund,
                                 i.e. canceled_order_paid or unavailable_order_paid.
                                 A freight refund does NOT get this action: the README
                                 worked example is a late_delivery_seller case refunding
                                 18.27 BRL and its action list omits it.
3. coordinate_multi_seller_case-- when the order has 2 or more distinct sellers
4. verify_payment_allocation   -- when the order has 2 or more payment rows,
                                  EXCEPT when primary is valid_split_payment
                                  (its primary action already explains the split)

case_status is "action_required" when recommended_refund_brl > 0, otherwise "no_action".
"""
