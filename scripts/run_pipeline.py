"""CP4 scaffolding: a FIXED-ORDER pipeline used to exercise Policy Agent + Verifier.

This driver hardcodes the call order on purpose so CP4 can be judged on its own. CP5
replaces it with the LLM supervisor, which decides the order itself; nothing in
src/agents depends on this file.

Usage:  python -m scripts.run_pipeline EC_001 EC_002 ...
"""

from __future__ import annotations

import json
import sys
import time

from src import config
from src.agents import verifier as verifier_mod
from src.agents.policy_agent import (
    POLICY_AGENT,
    build_case_brief,
    decision_from_findings,
    merge_facts,
)
from src.agents.specialists import SPECIALISTS
from src.llm.client import shared_client
from src.schema import build_output


def run_case(case_id: str, write: bool = True) -> dict:
    case = json.loads((config.INPUT_DIR / f"{case_id}.json").read_text())
    order_id = case["customer_request"]["claimed_order_id"]
    task = (
        f"Case {case_id}. The customer wrote: \"{case['customer_request']['message']}\"\n"
        f"The order under investigation is order_id={order_id}.\n"
        "Investigate your domain and file your findings."
    )

    print(f"\n{'=' * 76}\n{case_id}  order_id={order_id}")

    cards = {name: agent.run(task) for name, agent in SPECIALISTS.items()}
    for name, card in cards.items():
        if card.status != "ok":
            print(f"  !! {name} returned status={card.status}: {card.error}")

    brief = build_case_brief(case_id, order_id, cards)
    facts = merge_facts(cards)

    report = None
    output = None
    attempt = 0
    feedback = ""

    while attempt < config.VERIFIER_MAX_REPAIR_ROUNDS:
        attempt += 1
        policy_card = POLICY_AGENT.run(brief + feedback)
        if policy_card.status != "ok":
            print(f"  !! policy_agent status={policy_card.status}: {policy_card.error}")
            break

        all_facts = {**facts, **policy_card.facts}
        decision = decision_from_findings(policy_card.findings, all_facts)
        output = build_output(case_id, order_id, all_facts, decision)

        last_round = attempt >= config.VERIFIER_MAX_REPAIR_ROUNDS
        report = verifier_mod.verify(output, facts, order_id, apply_fallback=last_round)
        output = report.output

        verdict = "clean" if report.ok else f"{len(report.violations)} violation(s)"
        print(
            f"  attempt {attempt}: primary={decision['primary_issue']:24s} "
            f"refund={decision['recommended_refund_brl']:>8} -> {verdict}"
            + (f", {len(report.warnings)} warning(s)" if report.warnings else "")
        )
        for finding in report.findings:
            print(f"      [{finding.severity[:4]}] {finding.code}: {finding.message[:150]}")
        for note in report.normalizations:
            print(f"      [norm] {note}")

        if report.ok:
            break
        feedback = (
            "\n\nTHE VERIFIER REJECTED YOUR PREVIOUS ANSWER:\n"
            + report.feedback()
            + "\n\nReconsider from row 1 of the policy table and file corrected findings."
        )

    if output and write:
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = config.OUTPUT_DIR / f"{case_id}.json"
        path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
        print(f"  wrote {path.relative_to(config.ROOT)}")

    return {
        "case_id": case_id,
        "ok": bool(report and report.ok),
        "attempts": attempt,
        "fallback": bool(report and report.fallback_applied),
        "output": output,
    }


def main() -> int:
    case_ids = sys.argv[1:] or [f"EC_{i:03d}" for i in range(1, 6)]
    started = time.time()
    results = [run_case(cid) for cid in case_ids]

    clean = sum(1 for r in results if r["ok"])
    retried = sum(1 for r in results if r["attempts"] > 1)
    fallback = sum(1 for r in results if r["fallback"])

    print(f"\n{'=' * 76}")
    print(f"cases            : {len(results)}")
    print(f"verifier clean   : {clean}/{len(results)}")
    print(f"needed a retry   : {retried}")
    print(f"needed fallback  : {fallback}")
    print(f"elapsed          : {time.time() - started:.1f}s")
    print(f"usage            : {json.dumps(shared_client().usage.as_dict())}")
    return 0 if clean == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
