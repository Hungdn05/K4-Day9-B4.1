"""The four data-domain specialists.

Each one sees only its own toolbox, so its evidence card is the only way its domain
reaches the supervisor. That is the handoff required by README section 7.
"""

from __future__ import annotations

from src.agents.base import SpecialistAgent
from src.tools import CUSTOMER_TOOLS, DELIVERY_TOOLS, ORDER_TOOLS, PAYMENT_TOOLS

_COMMON_RULES = """\
Rules that apply to you:
- The data tools are the only truth. Never guess a value the tools did not return.
- Olist has no refund ledger, no transaction ids and no per-item tracking. If a tool
  does not return something, it does not exist -- say so, do not infer it.
- When a tool returns an error, read the message and fix your arguments.
- Report judgments only. Numbers and timestamps are carried to the supervisor
  automatically from the tool output; do not restate them.
"""


# --- Customer ----------------------------------------------------------------
CUSTOMER_AGENT = SpecialistAgent(
    name="customer_agent",
    toolbox=CUSTOMER_TOOLS,
    required_tools=("lookup_customer_identity", "list_customer_order_history"),
    system_prompt=f"""\
You are the Customer Agent in an e-commerce dispute investigation team.

Your only job: identify the shopper behind the disputed order and establish whether
they have bought before. You have no access to payments, delivery or products.

Procedure:
1. Call lookup_customer_identity with the disputed order_id to get customer_unique_id.
   Remember that in Olist each customer_id belongs to a single order, so identity across
   orders is carried by customer_unique_id, never by customer_id.
2. Call list_customer_order_history with that customer_unique_id, passing the disputed
   order as exclude_order_id so it is not counted as its own history.
3. Call report_findings.

The disputed order itself must never be counted as a related order.

{_COMMON_RULES}""",
    report_schema={
        "type": "object",
        "properties": {
            "is_repeat_customer": {
                "type": "boolean",
                "description": "True if this shopper has at least one OTHER order",
            },
            "related_order_count": {
                "type": "integer",
                "description": "How many other orders the shopper has",
            },
            "summary": {
                "type": "string",
                "description": "One sentence on who the customer is and their history",
            },
        },
        "required": ["is_repeat_customer", "related_order_count", "summary"],
        "additionalProperties": False,
    },
)


# --- Order & Product ---------------------------------------------------------
ORDER_PRODUCT_AGENT = SpecialistAgent(
    name="order_product_agent",
    toolbox=ORDER_TOOLS,
    required_tools=("get_order_core", "list_order_items", "get_order_product_context"),
    system_prompt=f"""\
You are the Order & Product Agent in an e-commerce dispute investigation team.

Your only job: establish the order header, its item rows, which sellers are on it, and
which products and categories it spans. You have no access to payments, delivery
timestamps or customer history.

Procedure:
1. get_order_core -- the order_status matters enormously: "canceled" and "unavailable"
   sit at the top of the policy table.
2. list_order_items -- note the item count and how many DISTINCT sellers appear.
3. get_order_product_context -- note how many distinct categories appear.
4. report_findings.

An order can legitimately have zero item rows. That is not an error and not something
to work around: report has_item_rows=false and let the team handle the null branch.

{_COMMON_RULES}""",
    report_schema={
        "type": "object",
        "properties": {
            "order_status": {
                "type": "string",
                "description": "The order_status exactly as returned by get_order_core",
            },
            "has_item_rows": {"type": "boolean", "description": "False when the order has no item rows"},
            "is_multi_item_order": {"type": "boolean", "description": "True when 2 or more item rows"},
            "is_multi_seller_order": {
                "type": "boolean",
                "description": "True when 2 or more DISTINCT seller_id values",
            },
            "has_multiple_categories": {
                "type": "boolean",
                "description": "True when 2 or more distinct product categories",
            },
            "summary": {"type": "string", "description": "One sentence on the order composition"},
        },
        "required": [
            "order_status",
            "has_item_rows",
            "is_multi_item_order",
            "is_multi_seller_order",
            "has_multiple_categories",
            "summary",
        ],
        "additionalProperties": False,
    },
)


# --- Payment -----------------------------------------------------------------
PAYMENT_AGENT = SpecialistAgent(
    name="payment_agent",
    toolbox=PAYMENT_TOOLS,
    required_tools=("list_order_payments", "reconcile_order_payment"),
    system_prompt=f"""\
You are the Payment Agent in an e-commerce dispute investigation team.

Your only job: enumerate the payment rows and reconcile them against item price plus
freight. You have no access to delivery timestamps or customer history.

Procedure:
1. list_order_payments -- count the rows. Two or more rows means a split payment.
   payment_value is the value of one payment ROW, not one installment; an order paid
   in 8 installments on one card is still a SINGLE payment row.
2. reconcile_order_payment -- read reconciled and difference_brl.
3. report_findings.

When the order has no item rows there is nothing to reconcile against: the tool returns
null for expected/difference/reconciled and you must report
reconciliation_status="not_applicable". Do not treat that as a mismatch.

{_COMMON_RULES}""",
    report_schema={
        "type": "object",
        "properties": {
            "payment_row_count": {"type": "integer", "description": "Number of payment rows"},
            "is_split_payment": {"type": "boolean", "description": "True when 2 or more payment rows"},
            "reconciliation_status": {
                "type": "string",
                "enum": ["reconciled", "not_reconciled", "not_applicable"],
                "description": (
                    "reconciled when |difference_brl| <= 0.10; not_applicable when the "
                    "order has no item rows"
                ),
            },
            "payment_total_is_positive": {
                "type": "boolean",
                "description": "True when the customer actually paid more than 0 BRL",
            },
            "summary": {"type": "string", "description": "One sentence on the payment picture"},
        },
        "required": [
            "payment_row_count",
            "is_split_payment",
            "reconciliation_status",
            "payment_total_is_positive",
            "summary",
        ],
        "additionalProperties": False,
    },
)


# --- Delivery ----------------------------------------------------------------
DELIVERY_AGENT = SpecialistAgent(
    name="delivery_agent",
    toolbox=DELIVERY_TOOLS,
    required_tools=("get_delivery_timeline", "analyze_seller_handoff"),
    system_prompt=f"""\
You are the Delivery Agent in an e-commerce dispute investigation team.

Your only job: decide whether the order arrived late and, if so, whether any seller
handed the goods to the carrier after their own deadline. That distinction is what
separates seller fault from logistics fault, so be precise.

Procedure:
1. get_delivery_timeline -- delivery_variance_hours > 0 means late. A null variance
   means the order was never delivered, so it cannot be a late delivery at all.
2. analyze_seller_handoff -- a seller handed off late when their
   handoff_variance_hours > 0. List every such seller, not just the worst one.
3. report_findings.

Being late and having a late seller handoff are independent facts. An order can be
delivered late with every seller on time (the carrier was slow), and an order can be
delivered on time even though a seller handed off late. Report what the tools show.

{_COMMON_RULES}""",
    report_schema={
        "type": "object",
        "properties": {
            "delivered_late": {
                "type": "boolean",
                "description": "True only when delivery_variance_hours > 0",
            },
            "was_delivered": {
                "type": "boolean",
                "description": "False when the order never reached the customer",
            },
            "any_late_handoff": {
                "type": "boolean",
                "description": "True when at least one seller has handoff_variance_hours > 0",
            },
            "late_handoff_seller_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Every seller_id with a late handoff, in the order the tool listed them",
            },
            "summary": {"type": "string", "description": "One sentence on what went wrong, or did not"},
        },
        "required": [
            "delivered_late",
            "was_delivered",
            "any_late_handoff",
            "late_handoff_seller_ids",
            "summary",
        ],
        "additionalProperties": False,
    },
)


SPECIALISTS = {
    agent.name: agent
    for agent in (CUSTOMER_AGENT, ORDER_PRODUCT_AGENT, PAYMENT_AGENT, DELIVERY_AGENT)
}
