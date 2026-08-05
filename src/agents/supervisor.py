"""Supervisor Agent -- an LLM that orchestrates, not a pipeline that pretends to.

There is no hardcoded call order anywhere in this file. The supervisor is handed the
team as tools, each described by what it can do, and it decides who to call, in what
order, whether to call several at once, and -- when the verifier pushes a case back --
which agent should have another go. The only thing bounding it is a step budget, which
is a runaway guard rather than a decision.

The one execution liberty taken: when the supervisor asks for several data specialists
in a single turn, they run concurrently. Those four agents share no state and read
disjoint toolboxes, so running them together changes the wall clock and nothing else.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from src import config
from src.agents import verifier as verifier_mod
from src.agents.base import AgentResult, _assistant_message, _tool_message
from src.agents.policy_agent import (
    POLICY_AGENT,
    build_case_brief,
    decision_from_findings,
    merge_facts,
)
from src.agents.specialists import SPECIALISTS
from src.llm.client import LLMClient, LLMError, LLMFatalError, shared_client
from src.schema import build_output
from src.trace import NullTrace, TraceWriter

DATA_SPECIALIST_TOOLS = {
    "delegate_to_customer_agent": "customer_agent",
    "delegate_to_order_product_agent": "order_product_agent",
    "delegate_to_payment_agent": "payment_agent",
    "delegate_to_delivery_agent": "delivery_agent",
}

# Which agent is best placed to fix each class of violation. Advice for the
# supervisor to weigh, not a routing table it is forced to obey.
LIKELY_OWNER = {
    "primary_condition": "policy_agent",
    "secondary_issues": "policy_agent",
    "refund_amount": "policy_agent",
    "root_cause_mismatch": "policy_agent",
    "root_cause_missing": "policy_agent",
    "responsible_party": "policy_agent",
    "resolution_actions": "policy_agent",
    "evidence_mismatch": "policy_agent",
    "evidence_format": "policy_agent",
    "primary_unknown": "policy_agent",
    "null_handling": "order_product_agent or payment_agent",
    "entity_mismatch": "order_product_agent or payment_agent",
    "timestamp_format": "delivery_agent",
    "schema": "policy_agent",
}

SYSTEM_PROMPT = """\
You are the Coordinator of an e-commerce dispute investigation team. A customer case
has landed on your desk and you own the outcome.

YOUR TEAM (each one reads a different slice of the data; none of them can see another's):
  delegate_to_customer_agent       -- who the shopper is and whether they have bought before
  delegate_to_order_product_agent  -- order status, item rows, sellers, products, categories
  delegate_to_payment_agent        -- payment rows and whether they reconcile with item+freight
  delegate_to_delivery_agent       -- was it late, and did any seller hand off to the carrier late
  delegate_to_policy_agent         -- applies EC_POLICY_V2 to the evidence and rules on the case
  verify_case                      -- an independent auditor checks the ruling against the evidence
  finalize_case                    -- close the case and file the report

HOW TO WORK
- You decide the order. Nothing is scheduled for you.
- The four data specialists are independent of each other, so you may call several in
  one turn and they will run at the same time.
- The Policy Agent cannot see the data. It rules on the evidence cards your specialists
  filed, so it needs their evidence before it can decide anything useful.
- verify_case checks the ruling against the evidence and reports violations (things
  that are wrong) and warnings (things worth reconsidering).
- When the verifier rejects the case, read each finding and decide who should fix it.
  Most rulings are the Policy Agent's to correct -- pass it specific guidance about
  what was wrong. But if a finding suggests the underlying evidence is wrong or
  missing, re-delegate to the data specialist that owns it instead.
- finalize_case only succeeds once the case survives verification.

You have a limited number of turns, so do not call an agent again when nothing has
changed since its last report. Work the case, then close it.
"""


@dataclass
class CaseState:
    case_id: str
    order_id: str
    task: str
    cards: dict[str, AgentResult] = field(default_factory=dict)
    policy_card: AgentResult | None = None
    decision: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    verification: verifier_mod.VerificationReport | None = None
    verify_count: int = 0
    delegate_counts: Counter = field(default_factory=Counter)
    call_sequence: list[str] = field(default_factory=list)
    parallel_batches: int = 0


@dataclass
class CaseRun:
    case_id: str
    ok: bool
    output: dict[str, Any] | None
    steps: int
    seconds: float
    call_sequence: list[str]
    parallel_batches: int
    verify_count: int
    fallback_applied: bool
    degraded_reason: str | None = None


class SupervisorAgent:
    def __init__(
        self,
        client: LLMClient | None = None,
        trace: TraceWriter | None = None,
        parallel: bool = True,
    ) -> None:
        self.client = client or shared_client()
        self.trace = trace or NullTrace()
        self.parallel = parallel

    # --- tool surface offered to the supervisor ------------------------------
    def _tools(self) -> list[dict[str, Any]]:
        def delegate(name: str, description: str) -> dict[str, Any]:
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "focus": {
                                "type": "string",
                                "description": "Optional instruction to pass to this specialist",
                            }
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            }

        return [
            delegate(
                "delegate_to_customer_agent",
                "Ask the Customer Agent to resolve the shopper's identity and prior orders.",
            ),
            delegate(
                "delegate_to_order_product_agent",
                "Ask the Order & Product Agent for order status, item rows, sellers, "
                "products and categories.",
            ),
            delegate(
                "delegate_to_payment_agent",
                "Ask the Payment Agent for the payment rows and the reconciliation against "
                "item price plus freight.",
            ),
            delegate(
                "delegate_to_delivery_agent",
                "Ask the Delivery Agent whether the order was late and whether any seller "
                "handed off to the carrier late.",
            ),
            {
                "type": "function",
                "function": {
                    "name": "delegate_to_policy_agent",
                    "description": (
                        "Ask the Policy Agent to apply EC_POLICY_V2 to the evidence gathered "
                        "so far and rule on the case. Pass guidance when you are asking it to "
                        "correct a previous ruling."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "guidance": {
                                "type": "string",
                                "description": "What to reconsider, e.g. verifier findings to address",
                            }
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "verify_case",
                    "description": (
                        "Have the Verifier audit the current ruling against the evidence. "
                        "Returns violations that must be fixed and warnings worth reconsidering."
                    ),
                    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finalize_case",
                    "description": "Close the case and file the report. Only works once verification passes.",
                    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                },
            },
        ]

    # --- tool implementations ------------------------------------------------
    def _run_specialist(self, state: CaseState, tool_name: str, focus: str) -> dict[str, Any]:
        agent_name = DATA_SPECIALIST_TOOLS[tool_name]
        agent = SPECIALISTS[agent_name]
        task = state.task + (f"\nThe coordinator adds: {focus}" if focus else "")

        started = time.time()
        card = agent.run(task)
        state.cards[agent_name] = card
        state.delegate_counts[agent_name] += 1

        self.trace.emit(
            "agent_message",
            case_id=state.case_id,
            sender=agent_name,
            recipient="supervisor",
            elapsed_s=round(time.time() - started, 2),
            **card.trace_dict(),
        )

        if card.status != "ok":
            return {"agent": agent_name, "status": card.status, "error": card.error}
        return {"agent": agent_name, "status": "ok", "findings": card.findings}

    def _run_policy(self, state: CaseState, guidance: str) -> dict[str, Any]:
        if not state.cards:
            return {
                "error": "No evidence has been gathered yet. The Policy Agent cannot rule "
                "on an empty file -- delegate to the data specialists first."
            }

        brief = build_case_brief(state.case_id, state.order_id, state.cards)
        missing = [name for name in SPECIALISTS if name not in state.cards]
        if missing:
            brief += f"\n\nNOTE: no evidence card was filed by: {', '.join(missing)}."
        if guidance:
            brief += f"\n\nTHE COORDINATOR ASKS YOU TO ADDRESS THIS:\n{guidance}"

        started = time.time()
        card = POLICY_AGENT.run(brief)
        state.policy_card = card
        state.delegate_counts["policy_agent"] += 1

        self.trace.emit(
            "agent_message",
            case_id=state.case_id,
            sender="policy_agent",
            recipient="supervisor",
            elapsed_s=round(time.time() - started, 2),
            guidance=guidance,
            **card.trace_dict(),
        )

        if card.status != "ok":
            return {"agent": "policy_agent", "status": card.status, "error": card.error}

        facts = {**merge_facts(state.cards), **card.facts}
        state.decision = decision_from_findings(card.findings, facts)
        state.output = build_output(state.case_id, state.order_id, facts, state.decision)
        state.verification = None

        return {
            "agent": "policy_agent",
            "status": "ok",
            "ruling": {
                "primary_issue": state.decision["primary_issue"],
                "secondary_issues": state.decision["secondary_issues"],
                "recommended_refund_brl": state.decision["recommended_refund_brl"],
                "resolution_actions": state.decision["resolution_actions"],
                "reason": state.decision["reason"][:400],
            },
        }

    def _run_verify(self, state: CaseState) -> dict[str, Any]:
        if state.output is None:
            return {"error": "There is no ruling to verify yet. Ask the Policy Agent first."}

        state.verify_count += 1
        last_round = state.verify_count >= config.VERIFIER_MAX_REPAIR_ROUNDS
        report = verifier_mod.verify(
            state.output,
            merge_facts(state.cards),
            state.order_id,
            apply_fallback=last_round,
        )
        state.verification = report
        state.output = report.output

        self.trace.emit(
            "agent_message",
            case_id=state.case_id,
            sender="verifier",
            recipient="supervisor",
            attempt=state.verify_count,
            **report.as_dict(),
        )

        return {
            "agent": "verifier",
            "passed": report.ok,
            "attempt": state.verify_count,
            "violations": [
                {
                    "code": f.code,
                    "message": f.message,
                    "likely_owner": LIKELY_OWNER.get(f.code, "policy_agent"),
                }
                for f in report.violations
            ],
            "warnings": [{"code": f.code, "message": f.message} for f in report.warnings],
            "note": (
                "This was the final permitted verification round."
                if last_round
                else f"You have {config.VERIFIER_MAX_REPAIR_ROUNDS - state.verify_count} round(s) left."
            ),
        }

    def _run_finalize(self, state: CaseState) -> tuple[dict[str, Any], bool]:
        if state.output is None:
            return {"error": "Nothing to file. The case has no ruling yet."}, False
        if state.verification is None:
            return {"error": "File the case only after verify_case has audited the ruling."}, False
        if not state.verification.ok and state.verify_count < config.VERIFIER_MAX_REPAIR_ROUNDS:
            return (
                {
                    "error": "The verifier still reports violations. Fix them and verify again "
                    "before filing.",
                    "violations": [f.code for f in state.verification.violations],
                },
                False,
            )
        return {"filed": True, "case_id": state.case_id}, True

    # --- main loop -----------------------------------------------------------
    def run(self, case: dict[str, Any], max_steps: int = config.SUPERVISOR_MAX_STEPS) -> CaseRun:
        case_id = case["case_id"]
        order_id = case["customer_request"]["claimed_order_id"]
        started = time.time()

        state = CaseState(
            case_id=case_id,
            order_id=order_id,
            task=(
                f"Case {case_id}. The customer wrote: "
                f"\"{case['customer_request']['message']}\"\n"
                f"The order under investigation is order_id={order_id}.\n"
                "Investigate your domain and file your findings."
            ),
        )

        scope = case.get("investigation_scope", {})
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"New case {case_id} under policy {case.get('policy_version')}.\n"
                    f"Customer message: \"{case['customer_request']['message']}\"\n"
                    f"Disputed order_id: {order_id}\n"
                    f"Investigation scope: customer history="
                    f"{scope.get('include_customer_history')}, product context="
                    f"{scope.get('include_product_context')}\n\n"
                    "Take the case."
                ),
            },
        ]

        self.trace.emit("case_start", case_id=case_id, order_id=order_id, policy=case.get("policy_version"))

        tools = self._tools()
        filed = False
        degraded: str | None = None
        steps = 0

        for step in range(1, max_steps + 1):
            steps = step
            try:
                message = self.client.chat(messages, tools=tools, reasoning=True)
            except LLMFatalError:
                self.trace.emit("case_aborted", case_id=case_id, reason="provider auth/billing")
                raise
            except LLMError as exc:
                degraded = f"supervisor LLM failed: {exc}"
                break

            messages.append(_assistant_message(message))
            calls = message.get("tool_calls") or []

            if not calls:
                self.trace.emit(
                    "supervisor_thought",
                    case_id=case_id,
                    step=step,
                    content=(message.get("content") or "")[:600],
                )
                messages.append(
                    {
                        "role": "user",
                        "content": "Act by calling a tool. Do not reply in prose.",
                    }
                )
                continue

            planned = [c.get("function", {}).get("name", "?") for c in calls]
            state.call_sequence.extend(planned)
            self.trace.emit("supervisor_decision", case_id=case_id, step=step, calls=planned)

            # Data specialists in the same turn are independent: run them together.
            parallel_calls = [
                c for c in calls if c.get("function", {}).get("name") in DATA_SPECIALIST_TOOLS
            ]
            results: dict[str, Any] = {}
            if self.parallel and len(parallel_calls) > 1:
                state.parallel_batches += 1
                with ThreadPoolExecutor(max_workers=len(parallel_calls)) as pool:
                    futures = {
                        pool.submit(
                            self._run_specialist,
                            state,
                            c["function"]["name"],
                            _arg(c, "focus"),
                        ): c.get("id")
                        for c in parallel_calls
                    }
                    for future, call_id in futures.items():
                        results[call_id] = future.result()

            for call in calls:
                call_id = call.get("id")
                name = call.get("function", {}).get("name", "")

                if call_id in results:
                    output = results[call_id]
                elif name in DATA_SPECIALIST_TOOLS:
                    output = self._run_specialist(state, name, _arg(call, "focus"))
                elif name == "delegate_to_policy_agent":
                    output = self._run_policy(state, _arg(call, "guidance"))
                elif name == "verify_case":
                    output = self._run_verify(state)
                elif name == "finalize_case":
                    output, filed = self._run_finalize(state)
                else:
                    output = {"error": f"unknown tool {name!r}"}

                messages.append(_tool_message(call, output))

            if filed:
                break

        if not filed:
            degraded = degraded or f"supervisor did not file within {max_steps} steps"
            if state.output is not None and (
                state.verification is None or not state.verification.ok
            ):
                # Salvage: audit once more with the mechanical repairs enabled so a
                # case that ran out of turns still leaves a schema-valid file.
                report = verifier_mod.verify(
                    state.output, merge_facts(state.cards), state.order_id, apply_fallback=True
                )
                state.verification = report
                state.output = report.output

        run = CaseRun(
            case_id=case_id,
            ok=bool(state.verification and state.verification.ok),
            output=state.output,
            steps=steps,
            seconds=round(time.time() - started, 2),
            call_sequence=state.call_sequence,
            parallel_batches=state.parallel_batches,
            verify_count=state.verify_count,
            fallback_applied=bool(state.verification and state.verification.fallback_applied),
            degraded_reason=degraded,
        )

        self.trace.emit(
            "case_end",
            case_id=case_id,
            ok=run.ok,
            steps=run.steps,
            seconds=run.seconds,
            call_sequence=run.call_sequence,
            verify_rounds=run.verify_count,
            fallback_applied=run.fallback_applied,
            degraded_reason=run.degraded_reason,
            primary_issue=(state.output or {}).get("case_assessment", {}).get("primary_issue"),
            delegate_counts=dict(state.delegate_counts),
        )
        return run


def _arg(call: dict[str, Any], key: str) -> str:
    try:
        args = json.loads(call.get("function", {}).get("arguments") or "{}")
    except json.JSONDecodeError:
        return ""
    value = args.get(key, "")
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
