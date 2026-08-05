"""CP6 audit: check the 50 submitted files independently of the agents that wrote them.

Recomputes the policy outcome straight from the CSVs and diffs it against what the
multi-agent run produced. This is a scoring rehearsal, not part of the pipeline --
nothing here feeds back into src/.

Usage:  python -m scripts.audit_outputs
"""

from __future__ import annotations

import json
from collections import Counter

from src import config, policy
from src.agents.verifier import expected_actions, expected_secondary_issues
from src.data.store import get_store
from src.schema import validate_structure
from src.tools.customer_tools import list_customer_order_history, lookup_customer_identity
from src.tools.delivery_tools import analyze_seller_handoff, get_delivery_timeline
from src.tools.order_tools import get_order_core, get_order_product_context, list_order_items
from src.tools.payment_tools import list_order_payments, reconcile_order_payment
from src.tools.policy_tools import build_evidence_ids, compute_refund_amount


def facts_for(order_id: str) -> dict:
    identity = lookup_customer_identity(order_id)
    return {
        "get_order_core": get_order_core(order_id),
        "list_order_items": list_order_items(order_id),
        "get_order_product_context": get_order_product_context(order_id),
        "list_order_payments": list_order_payments(order_id),
        "reconcile_order_payment": reconcile_order_payment(order_id),
        "get_delivery_timeline": get_delivery_timeline(order_id),
        "analyze_seller_handoff": analyze_seller_handoff(order_id),
        "lookup_customer_identity": identity,
        "list_customer_order_history": list_customer_order_history(
            identity["customer_unique_id"], order_id
        ),
    }


def baseline_primary(facts: dict) -> str:
    status = facts["get_order_core"]["order_status"]
    paid = (facts["reconcile_order_payment"]["payment_total_brl"] or 0) > 0
    late = facts["get_delivery_timeline"]["delivered_late"]
    late_sellers = facts["analyze_seller_handoff"]["late_handoff_seller_ids"]
    rows = facts["list_order_payments"]["payment_row_count"]
    reconciled = facts["reconcile_order_payment"]["reconciled"]

    if status == "canceled" and paid:
        return "canceled_order_paid"
    if status == "unavailable" and paid:
        return "unavailable_order_paid"
    if late and late_sellers:
        return "late_delivery_seller"
    if late:
        return "late_delivery_logistics"
    if rows >= 2 and reconciled is True:
        return "valid_split_payment"
    return "unsupported_late_claim"


def main() -> int:
    case_ids = sorted(path.stem for path in config.INPUT_DIR.glob("EC_*.json"))
    problems: list[str] = []
    issues: Counter[str] = Counter()
    baseline: Counter[str] = Counter()
    agree = 0

    print(f"auditing {len(case_ids)} cases\n")

    for case_id in case_ids:
        path = config.OUTPUT_DIR / f"{case_id}.json"
        if not path.exists():
            problems.append(f"{case_id}: output file missing")
            continue

        out = json.loads(path.read_text(encoding="utf-8"))
        case = json.loads((config.INPUT_DIR / f"{case_id}.json").read_text(encoding="utf-8"))
        order_id = case["customer_request"]["claimed_order_id"]
        facts = facts_for(order_id)

        for error in validate_structure(out):
            problems.append(f"{case_id}: schema {error}")

        if out.get("case_id") != case_id:
            problems.append(f"{case_id}: case_id field says {out.get('case_id')!r}")

        primary = out["case_assessment"]["primary_issue"]
        expected = baseline_primary(facts)
        issues[primary] += 1
        baseline[expected] += 1
        if primary == expected:
            agree += 1
        else:
            problems.append(f"{case_id}: primary_issue {primary!r} but baseline says {expected!r}")

        # secondary issues, refund, actions
        exp_secondary = expected_secondary_issues(facts)
        if out["case_assessment"]["secondary_issues"] != exp_secondary:
            problems.append(
                f"{case_id}: secondary_issues {out['case_assessment']['secondary_issues']} != {exp_secondary}"
            )

        basis = policy.REFUND_BASIS.get(primary, "none")
        exp_refund = compute_refund_amount(order_id, basis)["recommended_refund_brl"]
        got_refund = out["financial_resolution"]["recommended_refund_brl"]
        if abs(got_refund - exp_refund) > 0.001:
            problems.append(f"{case_id}: refund {got_refund} != {exp_refund}")

        exp_status = "action_required" if exp_refund > 0 else "no_action"
        if out["case_assessment"]["case_status"] != exp_status:
            problems.append(f"{case_id}: case_status {out['case_assessment']['case_status']} != {exp_status}")

        exp_actions = expected_actions(primary, facts, exp_refund)
        if out["resolution_actions"] != exp_actions:
            problems.append(f"{case_id}: actions {out['resolution_actions']} != {exp_actions}")

        # evidence
        causes = out["root_cause_analysis"]["ranked_causes"]
        sellers = [p["party_id"] for p in out["root_cause_analysis"]["responsible_parties"] if p["party_type"] == "seller"]
        if causes:
            exp_evidence = build_evidence_ids(order_id, causes[0]["cause_code"], sellers)["evidence_ids"]
            if out["evidence_ids"] != exp_evidence:
                problems.append(f"{case_id}: evidence_ids differ from the reconstructible set")

        # measured blocks must echo the tools exactly
        delivery = out["delivery_analysis"]
        tl, hf = facts["get_delivery_timeline"], facts["analyze_seller_handoff"]
        for field, truth in (
            ("delivered_at", tl["delivered_at"]),
            ("estimated_delivery_at", tl["estimated_delivery_at"]),
            ("carrier_handoff_at", tl["carrier_handoff_at"]),
            ("delivery_variance_hours", tl["delivery_variance_hours"]),
            ("late_handoff_seller_ids", hf["late_handoff_seller_ids"]),
            ("seller_handoff_analysis", hf["seller_handoff_analysis"]),
        ):
            if delivery.get(field) != truth:
                problems.append(f"{case_id}: delivery_analysis.{field} does not match the tool")

        recon_out, recon_truth = out["payment_reconciliation"], facts["reconcile_order_payment"]
        for field in ("item_total_brl", "freight_total_brl", "expected_total_brl",
                      "payment_total_brl", "difference_brl", "reconciled"):
            if recon_out.get(field) != recon_truth.get(field):
                problems.append(f"{case_id}: payment_reconciliation.{field} does not match the tool")

        # null branch
        if not facts["get_order_core"]["has_item_rows"]:
            if any(recon_out[f] is not None for f in ("item_total_brl", "expected_total_brl", "difference_brl", "reconciled")):
                problems.append(f"{case_id}: no item rows but reconciliation fields are not null")
            if out["affected_entities"]["item_ids"] or out["product_context"]["product_ids"]:
                problems.append(f"{case_id}: no item rows but entity arrays are not empty")

        # array caps
        for section, key in (
            ("affected_entities", "order_ids"), ("affected_entities", "item_ids"),
            ("affected_entities", "seller_ids"), ("affected_entities", "payment_ids"),
            ("customer_context", "related_order_ids"),
            ("product_context", "product_ids"), ("product_context", "category_names"),
            ("root_cause_analysis", "ranked_causes"), ("root_cause_analysis", "responsible_parties"),
        ):
            if len(out[section][key]) > config.ARRAY_LIMITS[key]:
                problems.append(f"{case_id}: {section}.{key} exceeds its cap")
        if len(out["evidence_ids"]) > 20 or len(out["resolution_actions"]) > 5:
            problems.append(f"{case_id}: evidence_ids or resolution_actions exceeds its cap")

        confidence = out["case_assessment"]["confidence"]
        if not 0.0 <= confidence <= 1.0:
            problems.append(f"{case_id}: confidence {confidence} out of range")

    # --- trace stats ---------------------------------------------------------
    print("primary issue distribution")
    print(f"  {'issue':26s} {'produced':>9} {'baseline':>9}")
    for name in policy.PRIMARY_ISSUES:
        print(f"  {name:26s} {issues[name]:>9} {baseline[name]:>9}")

    if config.TRACE_PATH.exists():
        records = [json.loads(line) for line in config.TRACE_PATH.read_text().splitlines() if line]
        ends = [r for r in records if r["event"] == "case_end"]
        sequences = Counter(" > ".join(r["call_sequence"]) for r in ends)
        print(f"\ntrace: {len(records)} records, {len(ends)} cases closed")
        print(f"  re-rulings needed  : {sum(1 for r in ends if r.get('verify_rounds', 0) > 1)}")
        print(f"  fallback applied   : {sum(1 for r in ends if r.get('fallback_applied'))}")
        print(f"  degraded           : {sum(1 for r in ends if r.get('degraded_reason'))}")
        print(f"  distinct call paths: {len(sequences)}")
        for seq, count in sequences.most_common(5):
            print(f"     {count:>3}x  {seq[:110]}")

    print(f"\n{'=' * 70}")
    print(f"files present   : {sum(1 for c in case_ids if (config.OUTPUT_DIR / f'{c}.json').exists())}/{len(case_ids)}")
    print(f"primary agrees  : {agree}/{len(case_ids)}")
    if problems:
        print(f"PROBLEMS ({len(problems)}):")
        for line in problems:
            print(f"  - {line}")
        return 1
    print("no problems found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
