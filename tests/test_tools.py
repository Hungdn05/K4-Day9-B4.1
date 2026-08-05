"""CP2 tool-layer tests, run against the real CSVs and the real 50 input cases.

Run:  python -m tests.test_tools
"""

from __future__ import annotations

import json
import traceback
from decimal import Decimal

from src import config
from src.data.store import get_store
from src.tools.calc import dec_sum, hours_between, parse_ts, round2
from src.tools.customer_tools import list_customer_order_history, lookup_customer_identity
from src.tools.delivery_tools import analyze_seller_handoff, get_delivery_timeline
from src.tools.order_tools import get_order_core, get_order_product_context, list_order_items
from src.tools.payment_tools import list_order_payments, reconcile_order_payment
from src.tools.policy_tools import build_evidence_ids, compute_refund_amount
from src.tools.registry import ToolError

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        FAILURES.append(f"{label} :: {detail}")
        print(f"  FAIL  {label} :: {detail}")


def case_order_ids() -> list[tuple[str, str]]:
    pairs = []
    for path in sorted(config.INPUT_DIR.glob("EC_*.json")):
        case = json.loads(path.read_text())
        pairs.append((case["case_id"], case["customer_request"]["claimed_order_id"]))
    return pairs


# --- calc --------------------------------------------------------------------
def test_calc() -> None:
    print("\n== calc ==")
    check("round half-up beats banker's rounding", round2("2.675") == 2.68, f"got {round2('2.675')}")
    check("float drift avoided", round2(dec_sum(["0.1", "0.2"])) == 0.30)
    check("null propagates", round2(None) is None and hours_between(None, "2018-01-01 00:00:00") is None)
    check(
        "positive variance means late",
        hours_between("2018-03-31 15:23:33", "2018-03-28 00:00:00") == 87.39,
        f"got {hours_between('2018-03-31 15:23:33', '2018-03-28 00:00:00')}",
    )
    check(
        "negative variance means early",
        hours_between("2018-03-27 00:00:00", "2018-03-28 00:00:00") == -24.0,
    )
    check("blank cell parses to None", parse_ts("") is None and parse_ts(None) is None)


# --- unknown order -----------------------------------------------------------
def test_unknown_order() -> None:
    print("\n== unknown order rejection ==")
    for fn in (get_order_core, get_delivery_timeline, reconcile_order_payment, lookup_customer_identity):
        try:
            fn("not_a_real_order_id")
            check(f"{fn.__name__} rejects unknown order", False, "no ToolError raised")
        except ToolError:
            check(f"{fn.__name__} rejects unknown order", True)


# --- no-item orders ----------------------------------------------------------
def test_no_item_orders(pairs: list[tuple[str, str]]) -> None:
    print("\n== orders with no item rows (the null branch) ==")
    store = get_store()
    empties = [(cid, oid) for cid, oid in pairs if not store.get_items(oid)]
    check("found the 6 no-item cases", len(empties) == 6, f"found {len(empties)}")

    for case_id, order_id in empties:
        rec = reconcile_order_payment(order_id)
        items = list_order_items(order_id)
        prod = get_order_product_context(order_id)
        handoff = analyze_seller_handoff(order_id)
        core = get_order_core(order_id)

        ok = (
            rec["expected_total_brl"] is None
            and rec["difference_brl"] is None
            and rec["reconciled"] is None
            # Summing zero item rows is 0.00, not unknown. README section 4 names only
            # expected/difference/reconciled as null.
            and rec["item_total_brl"] == 0
            and rec["freight_total_brl"] == 0
            and items["item_ids"] == []
            and items["seller_ids"] == []
            and prod["product_ids"] == []
            and prod["category_names"] == []
            and handoff["seller_handoff_analysis"] == []
            and handoff["late_handoff_seller_ids"] == []
        )
        check(
            f"{case_id} ({core['order_status']}) nulls + empty arrays",
            ok,
            json.dumps({k: rec[k] for k in ("expected_total_brl", "difference_brl", "reconciled")}),
        )
        check(
            f"{case_id} payment_total still reported ({rec['payment_total_brl']})",
            isinstance(rec["payment_total_brl"], float),
        )


# --- reconciliation ----------------------------------------------------------
def test_reconciliation(pairs: list[tuple[str, str]]) -> None:
    print("\n== payment reconciliation ==")
    store = get_store()
    mismatched = 0
    for case_id, order_id in pairs:
        rec = reconcile_order_payment(order_id)
        if not store.get_items(order_id):
            continue
        items = store.get_items(order_id)
        payments = store.get_payments(order_id)
        expected = dec_sum(r["price"] for r in items) + dec_sum(r["freight_value"] for r in items)
        paid = dec_sum(r["payment_value"] for r in payments)
        assert rec["expected_total_brl"] == round2(expected), case_id
        assert rec["difference_brl"] == round2(paid - expected), case_id
        assert rec["reconciled"] == (abs(paid - expected) <= Decimal("0.10")), case_id
        if not rec["reconciled"]:
            mismatched += 1
    check("expected/difference/reconciled recomputed independently for all cases", True)
    print(f"  info  cases where payments do NOT reconcile: {mismatched}")


# --- delivery ----------------------------------------------------------------
def test_delivery(pairs: list[tuple[str, str]]) -> None:
    print("\n== delivery + handoff ==")
    late = early = undelivered = 0
    seller_late = 0
    for case_id, order_id in pairs:
        tl = get_delivery_timeline(order_id)
        hf = analyze_seller_handoff(order_id)

        if tl["delivered_at"] is None:
            undelivered += 1
            assert tl["delivery_variance_hours"] is None, case_id
            assert tl["delivered_late"] is False, case_id
        elif tl["delivered_late"]:
            late += 1
            if hf["any_late_handoff"]:
                seller_late += 1
        else:
            early += 1

        for row in hf["seller_handoff_analysis"]:
            assert row["shipping_limit_at"] is not None, case_id
            if hf["carrier_handoff_at"] is None:
                assert row["handoff_variance_hours"] is None, case_id
                assert row["late_handoff"] is False, case_id
            else:
                assert row["late_handoff"] == (row["handoff_variance_hours"] > 0), case_id

    check("timeline/handoff invariants hold across all 50 cases", True)
    print(f"  info  delivered late {late} | on time {early} | never delivered {undelivered}")
    print(f"  info  of the {late} late: seller handoff late {seller_late}, carrier-only {late - seller_late}")


# --- customer ----------------------------------------------------------------
def test_customer(pairs: list[tuple[str, str]]) -> None:
    print("\n== customer identity + history ==")
    repeat = 0
    for case_id, order_id in pairs:
        ident = lookup_customer_identity(order_id)
        hist = list_customer_order_history(ident["customer_unique_id"], order_id)
        assert order_id not in hist["related_order_ids"], f"{case_id} leaked claimed order"
        assert len(set(hist["related_order_ids"])) == len(hist["related_order_ids"]), case_id
        if hist["is_repeat_customer"]:
            repeat += 1
    check("claimed order never leaks into related_order_ids", True)
    check("related_order_ids has no duplicates", True)
    print(f"  info  repeat customers: {repeat}/50")


# --- policy ------------------------------------------------------------------
def test_policy(pairs: list[tuple[str, str]]) -> None:
    print("\n== policy calculators ==")
    _, order_id = pairs[0]

    freight = compute_refund_amount(order_id, "freight_total")
    payment = compute_refund_amount(order_id, "payment_total")
    none_basis = compute_refund_amount(order_id, "none")
    check(
        "refund basis maps to the right total",
        freight["recommended_refund_brl"] == list_order_items(order_id)["freight_total_brl"]
        and payment["recommended_refund_brl"] == list_order_payments(order_id)["payment_total_brl"]
        and none_basis["recommended_refund_brl"] == 0.0,
    )
    check("case_status derives from the refund", none_basis["case_status"] == "no_action")

    try:
        compute_refund_amount(order_id, "made_up_basis")
        check("bad refund basis rejected", False, "no ToolError")
    except ToolError:
        check("bad refund basis rejected", True)

    ev = build_evidence_ids(order_id, "SELLER_HANDOFF_AFTER_LIMIT", ["not_a_seller"])
    check("hallucinated seller dropped from evidence", ev["rejected_seller_ids"] == ["not_a_seller"])
    check(
        "evidence starts with order and ends with policy",
        ev["evidence_ids"][0] == f"order:{order_id}"
        and ev["evidence_ids"][-1] == "policy:SELLER_HANDOFF_AFTER_LIMIT",
    )

    try:
        build_evidence_ids(order_id, "NOT_A_CODE")
        check("bad root cause code rejected", False, "no ToolError")
    except ToolError:
        check("bad root cause code rejected", True)

    # Every evidence id of every case must be reconstructible from the CSVs.
    store = get_store()
    bad: list[str] = []
    for case_id, oid in pairs:
        sellers = [r["seller_id"] for r in store.get_items(oid)][:1]
        for eid in build_evidence_ids(oid, "DELIVERY_WITHIN_ESTIMATE", sellers)["evidence_ids"]:
            kind, _, rest = eid.partition(":")
            if kind == "order" and store.get_order(rest) is None:
                bad.append(eid)
            elif kind == "item":
                o, _, seq = rest.rpartition(":")
                if not any(r["order_item_id"] == seq for r in store.get_items(o)):
                    bad.append(eid)
            elif kind == "payment":
                o, _, seq = rest.rpartition(":")
                if not any(r["payment_sequential"] == seq for r in store.get_payments(o)):
                    bad.append(eid)
            elif kind == "seller" and rest not in set(store.sellers["seller_id"]):
                bad.append(eid)
    check("every generated evidence id resolves to a real CSV row", not bad, str(bad[:5]))


# --- access control ----------------------------------------------------------
def test_access_control() -> None:
    print("\n== toolbox isolation ==")
    from src.tools import CUSTOMER_TOOLS, DELIVERY_TOOLS, ORDER_TOOLS, PAYMENT_TOOLS

    check("payment agent cannot read delivery", "get_delivery_timeline" not in PAYMENT_TOOLS.names())
    check("customer agent cannot read payments", "list_order_payments" not in CUSTOMER_TOOLS.names())
    check("delivery agent cannot read payments", "reconcile_order_payment" not in DELIVERY_TOOLS.names())
    check("order agent cannot read customer history", "list_customer_order_history" not in ORDER_TOOLS.names())

    err = PAYMENT_TOOLS.invoke("get_delivery_timeline", {"order_id": "x"})
    check("cross-domain call returns an error, not data", "error" in err, str(err))
    err2 = PAYMENT_TOOLS.invoke("list_order_payments", "{not json}")
    check("malformed arguments return an error", "error" in err2, str(err2))
    err3 = PAYMENT_TOOLS.invoke("list_order_payments", {})
    check("missing required argument returns an error", "error" in err3, str(err3))


def main() -> int:
    pairs = case_order_ids()
    print(f"loaded {len(pairs)} input cases")
    try:
        test_calc()
        test_unknown_order()
        test_no_item_orders(pairs)
        test_reconciliation(pairs)
        test_delivery(pairs)
        test_customer(pairs)
        test_policy(pairs)
        test_access_control()
    except AssertionError:
        traceback.print_exc()
        FAILURES.append("assertion error (see traceback)")

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print("all tool-layer checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
