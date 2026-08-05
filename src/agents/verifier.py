"""Verifier Agent -- deterministic, and deliberately not a decision maker.

Three distinct jobs, kept separate on purpose:

  NORMALIZE   Pure formatting the schema dictates and no one should be judged on:
              array ordering, de-duplication, truncation to the README caps,
              2-decimal rounding, case_status following the refund. Applied always.

  VIOLATION   The output contradicts the facts the specialists collected, or breaks
              the schema. Handed back to the supervisor to re-dispatch. The verifier
              never silently rewrites these -- an agent has to fix its own reasoning.

  WARNING     A critique, not a correction: "this order is canceled and was paid,
              did you consider rule 1 before choosing rule 4?". The Policy Agent
              remains free to disagree and keep its answer.

What the verifier does NOT do is derive the primary issue itself and overwrite the
model's choice. That would make the agents decorative.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from src import config, policy, schema
from src.tools.policy_tools import build_evidence_ids, compute_refund_amount

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
EVIDENCE_RE = re.compile(r"^(order:[0-9a-f]{32}|item:[0-9a-f]{32}:\d+|payment:[0-9a-f]{32}:\d+|seller:[0-9a-f]{32}|policy:[A-Z_]+)$")


@dataclass
class Finding:
    severity: str  # "violation" | "warning"
    code: str
    message: str


@dataclass
class VerificationReport:
    ok: bool
    findings: list[Finding] = field(default_factory=list)
    normalizations: list[str] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)
    fallback_applied: bool = False

    @property
    def violations(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "violation"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    def feedback(self) -> str:
        """The critique text the supervisor forwards to whoever must fix it."""
        lines = []
        if self.violations:
            lines.append("VIOLATIONS you must fix:")
            lines += [f"  - [{f.code}] {f.message}" for f in self.violations]
        if self.warnings:
            lines.append("WARNINGS to reconsider (you may keep your answer if you disagree):")
            lines += [f"  - [{f.code}] {f.message}" for f in self.warnings]
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "violation_count": len(self.violations),
            "warning_count": len(self.warnings),
            "findings": [asdict(f) for f in self.findings],
            "normalizations": self.normalizations,
            "fallback_applied": self.fallback_applied,
        }


# --- mechanical derivations used for cross-examination -----------------------
def expected_secondary_issues(facts: dict[str, Any]) -> list[str]:
    items = facts.get("list_order_items", {})
    payments = facts.get("list_order_payments", {})
    products = facts.get("get_order_product_context", {})
    history = facts.get("list_customer_order_history", {})

    flags = {
        "multi_item_order": items.get("item_count", 0) >= 2,
        "multi_seller_order": items.get("distinct_seller_count", 0) >= 2,
        "split_payment": payments.get("payment_row_count", 0) >= 2,
        "repeat_customer": bool(history.get("related_order_ids")),
        "multiple_categories": products.get("distinct_category_count", 0) >= 2,
    }
    return [name for name in policy.SECONDARY_ISSUE_ORDER if flags[name]]


def expected_actions(primary: str, facts: dict[str, Any], refund: float) -> list[str]:
    items = facts.get("list_order_items", {})
    payments = facts.get("list_order_payments", {})

    actions = [policy.PRIMARY_ACTIONS[primary]]
    if primary == "late_delivery_seller":
        actions.append("review_seller_handoff")
    elif primary == "late_delivery_logistics":
        actions.append("review_carrier_delay")
    if refund > 0:
        actions.append("verify_refund_completion")
    if items.get("distinct_seller_count", 0) >= 2:
        actions.append("coordinate_multi_seller_case")
    if payments.get("payment_row_count", 0) >= 2 and primary != "valid_split_payment":
        actions.append("verify_payment_allocation")
    return actions[: config.ARRAY_LIMITS["resolution_actions"]]


def higher_priority_rows_that_hold(primary: str, facts: dict[str, Any]) -> list[str]:
    """Which rows ABOVE the chosen one also satisfy their condition."""
    core = facts.get("get_order_core", {})
    recon = facts.get("reconcile_order_payment", {})
    timeline = facts.get("get_delivery_timeline", {})
    handoff = facts.get("analyze_seller_handoff", {})
    payments = facts.get("list_order_payments", {})

    status = core.get("order_status")
    paid = (recon.get("payment_total_brl") or 0) > 0
    late = bool(timeline.get("delivered_late"))
    late_sellers = handoff.get("late_handoff_seller_ids") or []

    holds = {
        "canceled_order_paid": status == "canceled" and paid,
        "unavailable_order_paid": status == "unavailable" and paid,
        "late_delivery_seller": late and bool(late_sellers),
        "late_delivery_logistics": late and not late_sellers,
        "valid_split_payment": payments.get("payment_row_count", 0) >= 2
        and recon.get("reconciled") is True,
        "unsupported_late_claim": True,
    }
    order = list(policy.PRIMARY_ISSUES)
    if primary not in order:
        return []
    return [name for name in order[: order.index(primary)] if holds[name]]


# --- normalization -----------------------------------------------------------
def _normalize(output: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    assessment = output.setdefault("case_assessment", {})

    seen: set[str] = set()
    ordered = [
        name
        for name in policy.SECONDARY_ISSUE_ORDER
        if name in assessment.get("secondary_issues", []) and not (name in seen or seen.add(name))
    ]
    if ordered != assessment.get("secondary_issues"):
        notes.append("reordered/deduplicated secondary_issues into policy order")
        assessment["secondary_issues"] = ordered

    actions = list(dict.fromkeys(output.get("resolution_actions", [])))
    if actions != output.get("resolution_actions"):
        notes.append("deduplicated resolution_actions")
    output["resolution_actions"] = actions[: config.ARRAY_LIMITS["resolution_actions"]]

    refund = float(output.get("financial_resolution", {}).get("recommended_refund_brl", 0.0))
    expected_status = "action_required" if refund > 0 else "no_action"
    if assessment.get("case_status") != expected_status:
        notes.append(f"case_status set to {expected_status} to match a refund of {refund}")
        assessment["case_status"] = expected_status

    confidence = assessment.get("confidence", 0.9)
    try:
        confidence = min(1.0, max(0.0, round(float(confidence), 2)))
    except (TypeError, ValueError):
        confidence = 0.5
        notes.append("confidence was not a number; defaulted to 0.5")
    assessment["confidence"] = confidence

    for index, cause in enumerate(output.get("root_cause_analysis", {}).get("ranked_causes", []), 1):
        if cause.get("rank") != index:
            notes.append("re-ranked ranked_causes to be 1..n")
            cause["rank"] = index

    for key, limit in config.ARRAY_LIMITS.items():
        for section in ("affected_entities", "customer_context", "product_context", "root_cause_analysis"):
            block = output.get(section, {})
            if key in block and len(block[key]) > limit:
                notes.append(f"{section}.{key} truncated to {limit}")
                block[key] = block[key][:limit]
    if len(output.get("evidence_ids", [])) > config.ARRAY_LIMITS["evidence_ids"]:
        notes.append("evidence_ids truncated to 20")
        output["evidence_ids"] = output["evidence_ids"][: config.ARRAY_LIMITS["evidence_ids"]]

    return output


# --- main entry point --------------------------------------------------------
def verify(
    output: dict[str, Any],
    facts: dict[str, Any],
    order_id: str,
    apply_fallback: bool = False,
) -> VerificationReport:
    report = VerificationReport(ok=False)
    output = _normalize(dict(output), report.normalizations)

    def violation(code: str, message: str) -> None:
        report.findings.append(Finding("violation", code, message))

    def warning(code: str, message: str) -> None:
        report.findings.append(Finding("warning", code, message))

    assessment = output.get("case_assessment", {})
    primary = assessment.get("primary_issue")
    refund = float(output.get("financial_resolution", {}).get("recommended_refund_brl", 0.0))

    core = facts.get("get_order_core", {})
    items = facts.get("list_order_items", {})
    payments = facts.get("list_order_payments", {})
    recon = facts.get("reconcile_order_payment", {})
    timeline = facts.get("get_delivery_timeline", {})
    handoff = facts.get("analyze_seller_handoff", {})

    # 1. schema shape
    for error in schema.validate_structure(output):
        violation("schema", error)

    if primary not in policy.PRIMARY_ISSUES:
        violation("primary_unknown", f"primary_issue {primary!r} is not in the policy table")
        report.output = output
        return report

    # 2. the chosen row's own conditions must actually hold
    status = core.get("order_status")
    paid = (recon.get("payment_total_brl") or 0) > 0
    late = bool(timeline.get("delivered_late"))
    late_sellers = handoff.get("late_handoff_seller_ids") or []
    pay_rows = payments.get("payment_row_count", 0)

    conditions = {
        "canceled_order_paid": (
            status == "canceled" and paid,
            f"requires order_status=canceled and payment>0, but status={status} paid={paid}",
        ),
        "unavailable_order_paid": (
            status == "unavailable" and paid,
            f"requires order_status=unavailable and payment>0, but status={status} paid={paid}",
        ),
        "late_delivery_seller": (
            late and bool(late_sellers),
            f"requires a late delivery AND a late seller handoff, but delivered_late={late} "
            f"late_handoff_seller_ids={late_sellers}",
        ),
        "late_delivery_logistics": (
            late and not late_sellers,
            f"requires a late delivery AND no late seller handoff, but delivered_late={late} "
            f"late_handoff_seller_ids={late_sellers}",
        ),
        "valid_split_payment": (
            pay_rows >= 2 and recon.get("reconciled") is True,
            f"requires 2+ payment rows AND reconciled=true, but rows={pay_rows} "
            f"reconciled={recon.get('reconciled')}",
        ),
        "unsupported_late_claim": (
            not late,
            f"requires the order NOT to be late, but delivered_late={late}",
        ),
    }
    holds, why = conditions[primary]
    if not holds:
        violation("primary_condition", f"{primary} {why}")

    # 3. critique: did a higher row already apply?
    skipped = higher_priority_rows_that_hold(primary, facts)
    if skipped:
        warning(
            "priority_order",
            f"you chose {primary}, but these higher-priority rows also hold and the policy "
            f"table must be read top-down: {', '.join(skipped)}",
        )

    # 4. secondary issues
    expected_secondary = expected_secondary_issues(facts)
    if assessment.get("secondary_issues") != expected_secondary:
        violation(
            "secondary_issues",
            f"secondary_issues should be {expected_secondary} given the facts, "
            f"got {assessment.get('secondary_issues')}",
        )

    # 5. refund follows the basis the table prescribes
    basis = policy.REFUND_BASIS[primary]
    truth_refund = compute_refund_amount(order_id, basis)["recommended_refund_brl"]
    if abs(refund - truth_refund) > 0.001:
        violation(
            "refund_amount",
            f"{primary} refunds the {basis} which is {truth_refund} BRL, got {refund}",
        )

    # 6. root cause + responsible parties
    causes = output.get("root_cause_analysis", {}).get("ranked_causes", [])
    if not causes:
        violation("root_cause_missing", "ranked_causes is empty")
    else:
        expected_code = {
            "canceled_order_paid": "ORDER_CANCELED_AFTER_PAYMENT",
            "unavailable_order_paid": "ORDER_UNAVAILABLE_AFTER_PAYMENT",
            "late_delivery_seller": "SELLER_HANDOFF_AFTER_LIMIT",
            "late_delivery_logistics": "CARRIER_DELIVERED_AFTER_ESTIMATE",
            "valid_split_payment": "MULTIPLE_PAYMENTS_RECONCILED",
            "unsupported_late_claim": "DELIVERY_WITHIN_ESTIMATE",
        }[primary]
        if causes[0].get("cause_code") != expected_code:
            violation(
                "root_cause_mismatch",
                f"{primary} has root cause {expected_code}, got {causes[0].get('cause_code')}",
            )

    parties = output.get("root_cause_analysis", {}).get("responsible_parties", [])
    party_types = {p.get("party_type") for p in parties}
    if primary in ("canceled_order_paid", "unavailable_order_paid"):
        if parties != [{"party_type": "platform", "party_id": policy.PLATFORM_PARTY_ID}]:
            violation(
                "responsible_party",
                f"{primary} is the platform's fault: expected exactly one party "
                f"platform/{policy.PLATFORM_PARTY_ID}, got {parties}",
            )
    elif primary == "late_delivery_seller":
        ids = [p.get("party_id") for p in parties]
        if party_types != {"seller"} or sorted(ids) != sorted(late_sellers[:3]):
            violation(
                "responsible_party",
                f"late_delivery_seller blames the late-handoff sellers {late_sellers[:3]}, got {parties}",
            )
    elif primary == "late_delivery_logistics":
        if parties != [
            {"party_type": "logistics_provider", "party_id": policy.LOGISTICS_PARTY_ID}
        ]:
            violation(
                "responsible_party",
                f"late_delivery_logistics blames logistics_provider/{policy.LOGISTICS_PARTY_ID}, got {parties}",
            )
    elif parties:
        violation("responsible_party", f"{primary} has no responsible party, got {parties}")

    # 7. actions
    expected_action_list = expected_actions(primary, facts, truth_refund)
    if output.get("resolution_actions") != expected_action_list:
        violation(
            "resolution_actions",
            f"resolution_actions should be {expected_action_list}, got {output.get('resolution_actions')}",
        )

    # 8. evidence ids: format, existence, and completeness
    responsible_sellers = [
        p["party_id"] for p in parties if p.get("party_type") == "seller"
    ]
    root_code = causes[0]["cause_code"] if causes else None
    if root_code in policy.ROOT_CAUSE_CODES:
        truth_evidence = build_evidence_ids(order_id, root_code, responsible_sellers)["evidence_ids"]
        if output.get("evidence_ids") != truth_evidence:
            violation(
                "evidence_mismatch",
                "evidence_ids must be exactly what build_evidence_ids returns for this "
                f"order and root cause ({len(truth_evidence)} ids), got {len(output.get('evidence_ids', []))}",
            )
    for eid in output.get("evidence_ids", []):
        if not EVIDENCE_RE.match(eid):
            violation("evidence_format", f"evidence id {eid!r} is malformed")

    # 9. null handling for orders without item rows
    if not core.get("has_item_rows", True):
        recon_block = output.get("payment_reconciliation", {})
        for key in ("item_total_brl", "freight_total_brl", "expected_total_brl", "difference_brl", "reconciled"):
            if recon_block.get(key) is not None:
                violation(
                    "null_handling",
                    f"order has no item rows so payment_reconciliation.{key} must be null",
                )
        entities = output.get("affected_entities", {})
        for key in ("item_ids", "seller_ids"):
            if entities.get(key):
                violation("null_handling", f"order has no item rows so affected_entities.{key} must be empty")
        product_block = output.get("product_context", {})
        for key in ("product_ids", "category_names"):
            if product_block.get(key):
                violation("null_handling", f"order has no item rows so product_context.{key} must be empty")
        if output.get("delivery_analysis", {}).get("seller_handoff_analysis"):
            violation("null_handling", "order has no item rows so seller_handoff_analysis must be empty")

    # 10. timestamps echo the CSV format
    delivery = output.get("delivery_analysis", {})
    for key in ("delivered_at", "estimated_delivery_at", "carrier_handoff_at"):
        value = delivery.get(key)
        if value is not None and not TS_RE.match(str(value)):
            violation("timestamp_format", f"delivery_analysis.{key}={value!r} is not YYYY-MM-DD HH:MM:SS")
    for row in delivery.get("seller_handoff_analysis", []):
        value = row.get("shipping_limit_at")
        if value is not None and not TS_RE.match(str(value)):
            violation("timestamp_format", f"shipping_limit_at={value!r} is not YYYY-MM-DD HH:MM:SS")

    # 11. facts that must be echoed verbatim, never re-typed
    if output.get("affected_entities", {}).get("order_ids") != [order_id]:
        violation("entity_mismatch", f"affected_entities.order_ids must be exactly [{order_id!r}]")
    if output.get("affected_entities", {}).get("item_ids") != items.get("item_ids", [])[:5]:
        violation("entity_mismatch", "affected_entities.item_ids does not match the item rows")
    if output.get("affected_entities", {}).get("payment_ids") != payments.get("payment_ids", [])[:5]:
        violation("entity_mismatch", "affected_entities.payment_ids does not match the payment rows")

    # --- last-resort repair of the mechanical fields --------------------------
    # Only reached once the supervisor has spent its repair rounds. Rewrites the
    # fields that are pure functions of the facts; never touches primary_issue,
    # which is the one genuinely judgemental field. Every rewrite is recorded so
    # the run report can state how often the agents needed catching.
    repairable = {"secondary_issues", "resolution_actions", "refund_amount"}
    codes = {f.code for f in report.violations}
    if apply_fallback and (codes & repairable):
        notes = list(report.normalizations)
        if "secondary_issues" in codes:
            assessment["secondary_issues"] = expected_secondary
            notes.append("FALLBACK: secondary_issues rewritten from facts")
        if "resolution_actions" in codes:
            output["resolution_actions"] = expected_action_list
            notes.append("FALLBACK: resolution_actions rewritten from facts")
        if "refund_amount" in codes:
            output["financial_resolution"]["recommended_refund_brl"] = truth_refund
            notes.append("FALLBACK: refund recomputed from the policy basis")

        rerun = verify(output, facts, order_id, apply_fallback=False)
        rerun.normalizations = notes + [
            n for n in rerun.normalizations if n not in notes
        ]
        rerun.fallback_applied = True
        return rerun

    report.output = output
    report.ok = not report.violations
    return report
