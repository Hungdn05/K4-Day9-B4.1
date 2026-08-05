"""CP3 harness: run the four specialists on a case and print their A2A messages.

Usage:  python -m scripts.run_specialists [CASE_ID ...]
"""

from __future__ import annotations

import json
import sys
import time

from src import config
from src.agents.specialists import SPECIALISTS
from src.llm.client import shared_client


def load_case(case_id: str) -> dict:
    return json.loads((config.INPUT_DIR / f"{case_id}.json").read_text())


def run_case(case_id: str) -> dict:
    case = load_case(case_id)
    order_id = case["customer_request"]["claimed_order_id"]
    task = (
        f"Case {case_id}. The customer wrote: \"{case['customer_request']['message']}\"\n"
        f"The order under investigation is order_id={order_id}.\n"
        "Investigate your domain and file your findings."
    )

    print(f"\n{'#' * 72}\n# {case_id}  order_id={order_id}\n{'#' * 72}")
    results = {}
    for name, agent in SPECIALISTS.items():
        started = time.time()
        result = agent.run(task)
        elapsed = time.time() - started
        results[name] = result

        flag = {"ok": "OK ", "incomplete": "!! ", "error": "XX "}.get(result.status, "?? ")
        print(f"\n--- {flag} {name}  ({result.steps} steps, {elapsed:.1f}s) ---")
        for record in result.tool_calls:
            mark = "->" if record.ok else "!!"
            print(f"  {mark} {record.tool}{json.dumps(record.arguments, ensure_ascii=False)}")
            if not record.ok:
                print(f"     rejected: {record.result_preview}")
        if result.error:
            print(f"  error: {result.error}")
        print("  findings: " + json.dumps(result.findings, ensure_ascii=False, indent=2).replace("\n", "\n  "))
    return results


def main() -> int:
    case_ids = sys.argv[1:] or ["EC_001"]
    started = time.time()
    for case_id in case_ids:
        run_case(case_id)
    print(f"\n{'=' * 72}")
    print(f"{len(case_ids)} case(s) in {time.time() - started:.1f}s")
    print(f"usage: {json.dumps(shared_client().usage.as_dict())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
