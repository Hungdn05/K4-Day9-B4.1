# K4 Day 09 — Multi-Agent Architecture

## Objective and operating principle

The system resolves one claimed Olist order per input case.  Agents make
claims only from records they received or from deterministic calculations over
those records.  Customer text supplies the order ID and language only; it is
not treated as proof of a delivery, refund, tracking, or payment event.

All agents use the configured `gpt-4o-mini` model when natural language
reasoning is needed. Its 8B parameter figure is supplied by the project owner;
OpenAI does not publish parameter counts publicly. It is therefore configured
as within the lab's 10B limit by project instruction. Parsing, joins, monetary
arithmetic, timestamps, policy selection, and output verification are
deterministic Python operations so results remain repeatable and auditable.
During the official batch run, every handoff is also reviewed through the
Responses API. Model audit text is recorded in the trace but cannot override
source facts, policy, money, or evidence.

## Agents and least-privilege data access

| Agent | May read | Produces | May not decide |
| --- | --- | --- | --- |
| Coordinator | input case, validated handoffs | work plan and final assembly request | facts outside handoffs or policy outcome |
| Customer | `orders`, `customers` | canonical customer and related orders | delivery, payment, refunds |
| Order & Product | `orders`, `order_items`, `products`, `sellers` | ordered items, sellers, products, categories, shipping limits | payment or delivery responsibility |
| Payment | `order_items`, `order_payments` | totals, difference, reconciliation, payment IDs/types | delivery root cause |
| Delivery | `orders`, `order_items` | timestamps, delivery variance, seller handoff variance | refund amount or customer history |
| Policy | normalized domain facts and policy version only | primary/secondary issue, root cause, responsible parties, refund, actions | raw CSV lookup |
| Verifier | input case, final draft, source IDs named in the draft | pass/fail findings | amend facts or invent evidence |

The coordinator reads no bulk CSV.  Dataset-specific agents receive the
claimed `order_id` and query only their permitted datasets.  Each worker
returns a typed handoff; the coordinator gives the policy agent only the
normalized `CaseFacts` aggregate.  The verifier independently reconstructs
each evidence ID from the already collected source records and validates
array limits, null handling, numeric rounding and the output schema.

## Handoff contract and flow

Every handoff is a JSON-compatible `AgentHandoff` with `case_id`, `agent`,
`payload`, stable `source_record_ids`, and UTC `created_at`.  The payload is
domain scoped.  The data IDs make it possible to produce `logging/trace.jsonl`
later without scraping prompts or exposing secrets.

```text
input JSON
    │ validate contract
    ▼
Coordinator ──► Customer ────────┐
    ├────────► Order & Product ──┼──► normalized CaseFacts ──► Policy
    ├────────► Payment ──────────┤                                │
    └────────► Delivery ─────────┘                                ▼
                                      Coordinator assembles draft JSON
                                                      │
                                                      ▼
                                               Verifier ──► output JSON
```

The first four worker handoffs can run concurrently because they are
independent.  Policy waits for all facts.  Verification is deliberately a
separate final stage, preventing an investigating agent from approving its own
conclusion.

## Policy boundary

`src/ecommerce_dispute/policy.py` contains the complete ordered selection of
the documented `EC_POLICY_V2` primary issues.  It takes explicit Boolean or
`None` facts rather than inspecting raw rows.  A `None` is treated as unknown,
not as `False`; if no documented rule can be proven, it raises a policy error
instead of fabricating an outcome.  This keeps unsupported cases visible for
review.

Secondary issues are appended only in the specified order.  Refund and action
selection are derived in the same policy module.  Checkpoint 3 will build
`CaseFacts` from the agent handoffs and serialize a policy decision into the
required output schema.
