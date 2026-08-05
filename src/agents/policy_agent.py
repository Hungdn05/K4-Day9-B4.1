"""Policy Agent -- the one agent that is asked to reason rather than to look up.

It never sees the CSVs. It sees the evidence cards the four specialists filed, and
it applies EC_POLICY_V2 to them. Thinking is enabled here (and only here among the
specialists) because reading a priority-ordered table top-down is exactly the kind
of step an 8B model skips when it answers from the first pattern it recognises.
"""

from __future__ import annotations

import json
from typing import Any

from src import policy
from src.agents.base import AgentResult, SpecialistAgent
from src.tools import POLICY_TOOLS

SYSTEM_PROMPT = f"""\
You are the Policy Agent in an e-commerce dispute investigation team. Four specialists
have already read the data and filed evidence cards. You do not have access to the raw
CSVs -- you decide, using their evidence and the policy below.

{policy.POLICY_TABLE_TEXT}

{policy.SECONDARY_RULES_TEXT}

{policy.ACTION_RULES_TEXT}

HOW TO WORK
1. Walk the primary issue table from row 1 to row 6. For each row, state to yourself
   whether its condition holds given the evidence. STOP at the first row that holds.
   A canceled and paid order is canceled_order_paid even if it also has three payment
   rows and even if it was never delivered. Do not jump to the row that "feels" like
   the customer's complaint.
2. Collect the secondary issues that hold, in the fixed order.
3. Call compute_refund_amount with the refund basis that YOUR chosen row prescribes:
   payment_total for rows 1-2, freight_total for rows 3-4, none for rows 5-6.
4. Call build_evidence_ids with your chosen root cause code and the seller ids you hold
   responsible (an empty list when nobody is responsible).
5. Call report_findings.

RULES
- Never invent a number, a timestamp, a seller id or an evidence id. The tools produce
  them; you choose which ones apply.
- Olist has no refund ledger, no transaction ids and no per-item delivery proof. Do not
  reason about events the evidence does not contain.
- responsible_seller_ids may only contain sellers the Delivery Agent reported as having
  handed off late, and only when you chose late_delivery_seller.
- If the verifier sends the case back, read every finding. Fix the violations. For the
  warnings, either fix them or explain in primary_issue_reason why you still disagree.
- confidence reflects how cleanly the evidence fits the row you chose: about 0.95 when
  the row is unambiguous, lower when the evidence is thin or contradictory.
"""

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "primary_issue": {
            "type": "string",
            "enum": list(policy.PRIMARY_ISSUES),
            "description": "The FIRST row of the policy table whose condition holds",
        },
        "primary_issue_reason": {
            "type": "string",
            "description": "Which rows above the chosen one you rejected, and why",
        },
        "secondary_issues": {
            "type": "array",
            "items": {"type": "string", "enum": list(policy.SECONDARY_ISSUE_ORDER)},
            "description": "Applicable secondary issues in the fixed policy order",
        },
        "root_cause_code": {
            "type": "string",
            "enum": list(policy.ROOT_CAUSE_CODES),
            "description": "The rank-1 root cause implied by your chosen row",
        },
        "responsible_party_type": {
            "type": "string",
            "enum": ["platform", "seller", "logistics_provider", "none"],
            "description": "Who the policy holds responsible for your chosen row",
        },
        "responsible_seller_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Late-handoff sellers, only when responsible_party_type is seller",
        },
        "resolution_actions": {
            "type": "array",
            "items": {"type": "string", "enum": list(policy.ALL_ACTIONS)},
            "description": "Primary action first, then supplementary actions in the fixed order",
        },
        "confidence": {
            "type": "number",
            "description": "0 to 1, how cleanly the evidence fits the chosen row",
        },
    },
    "required": [
        "primary_issue",
        "primary_issue_reason",
        "secondary_issues",
        "root_cause_code",
        "responsible_party_type",
        "resolution_actions",
        "confidence",
    ],
    "additionalProperties": False,
}

POLICY_AGENT = SpecialistAgent(
    name="policy_agent",
    toolbox=POLICY_TOOLS,
    system_prompt=SYSTEM_PROMPT,
    report_schema=REPORT_SCHEMA,
    required_tools=("compute_refund_amount", "build_evidence_ids"),
    reasoning=True,
)


def build_case_brief(case_id: str, order_id: str, cards: dict[str, AgentResult]) -> str:
    """Render the specialists' evidence cards into the Policy Agent's briefing.

    Built by code from the cards, so what the Policy Agent reads is exactly what the
    specialists filed -- no intermediate model gets to paraphrase the numbers.
    """
    lines = [f"CASE {case_id} -- order_id={order_id}", "", "EVIDENCE CARDS FROM THE TEAM:"]

    for name, card in cards.items():
        lines.append(f"\n[{name}] status={card.status}")
        if card.status != "ok":
            lines.append(f"  UNAVAILABLE: {card.error}")
            continue
        for key, value in card.findings.items():
            lines.append(f"  {key}: {json.dumps(value, ensure_ascii=False)}")

    facts = merge_facts(cards)
    timeline = facts.get("get_delivery_timeline", {})
    recon = facts.get("reconcile_order_payment", {})
    items = facts.get("list_order_items", {})
    payments = facts.get("list_order_payments", {})
    handoff = facts.get("analyze_seller_handoff", {})

    lines += [
        "",
        "MEASURED FIGURES (taken straight from the tools, already rounded):",
        f"  order_status              : {facts.get('get_order_core', {}).get('order_status')}",
        f"  item_row_count            : {items.get('item_count', 0)}",
        f"  distinct_seller_count     : {items.get('distinct_seller_count', 0)}",
        f"  payment_row_count         : {payments.get('payment_row_count', 0)}",
        f"  payment_total_brl         : {recon.get('payment_total_brl')}",
        f"  freight_total_brl         : {recon.get('freight_total_brl')}",
        f"  expected_total_brl        : {recon.get('expected_total_brl')}",
        f"  difference_brl            : {recon.get('difference_brl')}",
        f"  reconciled                : {recon.get('reconciled')}",
        f"  delivery_variance_hours   : {timeline.get('delivery_variance_hours')}",
        f"  delivered_at              : {timeline.get('delivered_at')}",
        f"  estimated_delivery_at     : {timeline.get('estimated_delivery_at')}",
        f"  late_handoff_seller_ids   : {handoff.get('late_handoff_seller_ids', [])}",
        "",
        "Apply EC_POLICY_V2 top-down and file your findings.",
    ]
    return "\n".join(lines)


def merge_facts(cards: dict[str, AgentResult]) -> dict[str, Any]:
    """Flatten every specialist's tool results into one fact table."""
    facts: dict[str, Any] = {}
    for card in cards.values():
        facts.update(card.facts)
    return facts


def decision_from_findings(findings: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    """Turn the Policy Agent's report into the decision block `build_output` expects."""
    party_type = findings.get("responsible_party_type", "none")
    seller_ids = findings.get("responsible_seller_ids") or []

    if party_type == "platform":
        parties = [{"party_type": "platform", "party_id": policy.PLATFORM_PARTY_ID}]
    elif party_type == "logistics_provider":
        parties = [
            {"party_type": "logistics_provider", "party_id": policy.LOGISTICS_PARTY_ID}
        ]
    elif party_type == "seller":
        parties = [{"party_type": "seller", "party_id": sid} for sid in seller_ids[:3]]
    else:
        parties = []

    refund = facts.get("compute_refund_amount", {}).get("recommended_refund_brl", 0.0)
    evidence = facts.get("build_evidence_ids", {}).get("evidence_ids", [])

    return {
        "primary_issue": findings.get("primary_issue"),
        "secondary_issues": findings.get("secondary_issues", []),
        "ranked_cause_codes": [findings.get("root_cause_code")],
        "responsible_parties": parties,
        "resolution_actions": findings.get("resolution_actions", []),
        "recommended_refund_brl": refund,
        "evidence_ids": evidence,
        "confidence": findings.get("confidence", 0.9),
        "reason": findings.get("primary_issue_reason", ""),
    }
