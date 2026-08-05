"""Write logging/metadata.json from config plus the statistics of the latest trace.

Usage:  python -m scripts.write_metadata
"""

from __future__ import annotations

import json
import platform
import sys
from collections import Counter

from src import config


def trace_stats() -> dict:
    if not config.TRACE_PATH.exists():
        return {}
    records = [json.loads(line) for line in config.TRACE_PATH.read_text().splitlines() if line]
    ends = [r for r in records if r["event"] == "case_end"]
    agent_messages = [r for r in records if r["event"] == "agent_message"]
    senders = Counter(r["sender"] for r in agent_messages)
    paths = Counter(" > ".join(r.get("call_sequence", [])) for r in ends)

    return {
        "cases_completed": len(ends),
        "trace_records": len(records),
        "agent_messages": len(agent_messages),
        "messages_by_agent": dict(senders),
        "distinct_supervisor_call_paths": len(paths),
        "cases_needing_re_ruling": sum(1 for r in ends if r.get("verify_rounds", 0) > 1),
        "cases_needing_fallback": sum(1 for r in ends if r.get("fallback_applied")),
        "cases_degraded": sum(1 for r in ends if r.get("degraded_reason")),
        "median_supervisor_steps": sorted(r.get("steps", 0) for r in ends)[len(ends) // 2]
        if ends
        else 0,
        "total_seconds": round(sum(r.get("seconds", 0) for r in ends), 1),
    }


def main() -> int:
    metadata = {
        "model": {
            "name": config.MODEL_NAME,
            "parameter_size": config.MODEL_PARAM_SIZE,
            "provider": config.PROVIDER,
            "api_base": config.API_BASE,
            "within_10b_limit": True,
            "note": "Every agent in the system runs on this single model.",
        },
        "decoding": {
            "temperature": config.TEMPERATURE,
            "top_p": config.TOP_P,
            "max_tokens": config.MAX_TOKENS,
            "seed": config.SEED,
            "reasoning_enabled_for": ["supervisor", "policy_agent"],
        },
        "framework": {
            "name": "custom",
            "description": "Hand-written tool-calling loop over the OpenRouter chat "
            "completions API. No agent framework (no LangChain, LangGraph, CrewAI or "
            "AutoGen) is used.",
            "libraries": ["requests", "pandas", "pydantic", "python-dotenv"],
        },
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "orchestration": "LLM supervisor with agents exposed as tools",
            "supervisor_max_steps": config.SUPERVISOR_MAX_STEPS,
            "specialist_max_steps": config.SPECIALIST_MAX_STEPS,
            "verifier_max_repair_rounds": config.VERIFIER_MAX_REPAIR_ROUNDS,
        },
        "agents": [
            {"name": "supervisor", "role": "orchestration", "llm": True, "tools": "agents-as-tools"},
            {"name": "customer_agent", "role": "customer identity and history", "llm": True, "toolbox": "customer"},
            {"name": "order_product_agent", "role": "order, items, sellers, products", "llm": True, "toolbox": "order_product"},
            {"name": "payment_agent", "role": "payments and reconciliation", "llm": True, "toolbox": "payment"},
            {"name": "delivery_agent", "role": "delivery and seller handoff variance", "llm": True, "toolbox": "delivery"},
            {"name": "policy_agent", "role": "applies EC_POLICY_V2", "llm": True, "toolbox": "policy"},
            {"name": "verifier", "role": "deterministic schema and consistency audit", "llm": False},
        ],
        "policy_version": config.POLICY_VERSION,
        "latest_run": trace_stats(),
    }

    config.METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.METADATA_PATH.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {config.METADATA_PATH.relative_to(config.ROOT)}")
    print(json.dumps(metadata["latest_run"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
