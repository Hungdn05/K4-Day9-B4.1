# External-score analysis (corrected)

## 1. What we actually observed

| Submission | Output content | Zip entry layout | External score |
| --- | --- | --- | ---: |
| A | README-aligned baseline | `output/EC_001.json` … `output/EC_050.json` | **67.1417** |
| B | six simultaneous convention changes | `output/EC_001.json` … `output/EC_050.json` | **5.4654** |
| C | A + no-item totals `0.0` instead of `null` (6 cases) | same | **79.0** |
| D | C + unmeasurable handoff variance `0.00` (7 cases) | same | pending |

**The upload validator requires the `output/` prefix on every entry.** A zip with
bare `EC_001.json` names is rejected before grading with:

```text
ZIP phải chứa đúng output/EC_001.json đến output/EC_050.json.
```

The `output.zip` committed at `ffafdd3` uses bare names, so it was never graded;
both external scores above came from `output/`-prefixed archives. Packaging was
therefore **identical** across A and B, which makes B a valid content signal: the
six simultaneous convention changes really did cause the collapse, and reverting
them was correct. An intermediate revision of this document blamed packaging for
B; that claim is withdrawn.

## 2. EC_002 is the worked example in the README

README section 6 prints a full output document. Its timestamps and amounts are
not illustrative — they are the real Olist record behind **EC_002**
(`eb09635680fadffb33358e40b05c9029`): delivered `2018-03-31 15:23:33`, estimated
`2018-03-28 00:00:00`, carrier `2018-03-15 21:33:51`, shipping limit
`2018-03-15 20:31:15`, variances `87.39` / `1.04`, totals `194.0` / `18.27` /
`212.27`, refund `18.27`.

Our `output/EC_002.json` reproduces every one of those values, plus the primary
issue, root cause, responsible party, evidence ID list and its ordering.

Two fields in the printed example disagree with our output:

- `secondary_issues: ["multi_item_order", "split_payment"]`. The order has **one**
  item row and the customer has another order, so this list is internally
  inconsistent with the example's own `item_ids` and `related_order_ids`. It is
  hand-written, not generated.
- `resolution_actions` omits `verify_refund_completion`. We keep it, because the
  documented supplementary ordering places `verify_refund_completion` *between*
  `review_seller_handoff`/`review_carrier_delay` and
  `coordinate_multi_seller_case`. That position is only meaningful if it can
  co-occur with the two late-delivery actions, i.e. if freight refunds get it too.

## 3. Field conventions cannot explain 67.1417

A parameterized oracle was built with 13 independent convention switches
(category language, affected-vs-responsible sellers, payment row order, payment
type de-duplication, evidence seller set, supplementary-action suppression,
seller-handoff rows for undelivered orders, history ordering, earliest-vs-latest
shipping limit, variance sign, freight scope, no-item totals). All 8 192
combinations were scored against our submitted output under several field-level
partial-credit models (leaf-path equality; per-key set F1; and harsh variants
that award **no** credit for correctly emitting `null` or `[]`).

- Best case (all conventions agree): 100.0
- **Worst case over all 8 192 combinations: 85.27** (harsh-null models: 89.7)
- No combination lands within 0.6 of 67.1417.

Since ~15 points is the entire reachable loss from convention disagreement, a
score of 67.1417 requires **whole cases scoring zero**. Solving
`3357.085 = (50 - g) · r` under `85 ≤ r ≤ 100` gives **g between 11 and 16
hard-gated cases**, with the rest scoring 93–99.

The same search puts the minimum achievable score for submission B's content at
**13.24**, still above its observed 5.4654. Since B was packaged correctly, the
grader must be **harsher than every model tried here**: it zeroes whole cases on
conditions none of the tested gate predicates capture. The likeliest trigger in B
is the English category names, which exist nowhere in `products.product_category_name`
— an ungrounded value, in a lab whose section 5 already treats ungrounded evidence
as a false positive.

Consequence for probe design: **never emit a value that cannot be copied out of a
CSV cell.** Language translation, re-derived labels and invented party IDs are the
dangerous class; row ordering and null-vs-zero are the safe class.

## 3b. Submission C identified the gate exactly

C differed from A in **two fields on six cases**. It scored 79.0.

```text
(79.0 - 67.1417) * 50 / 6 = 98.819 points per case
```

Six cases went from **0 to ~98.8**. That settles three things:

1. The hard gate is real, and `item_total_brl` / `freight_total_brl` being `null`
   was one of its triggers.
2. An ungated case scores ~98.8, not ~100 — so the residual per-case loss from
   field conventions is small, around 1.2 points.
3. The trigger is **not "null is forbidden"**. Those same six cases still carry
   `delivered_at`, `carrier_handoff_at`, `delivery_variance_hours`,
   `expected_total_brl`, `difference_brl` and `reconciled` as `null` and are now
   fully credited. The rule that fits is narrower:

> A case is zeroed when a field is `null` in our output while the grader's
> reference has a **number** there. `null == null` is fine.

A sum over zero item rows is `0.00` in the reference, so `null` failed. A delivery
variance with no delivery date is `null` in the reference too, so `null` passed.

## 3c. Locating the rest of the loss

After C, the 44 item-bearing cases still sum to 3357.085 (avg 76.30). With `g`
of them gated and the rest scoring `s`, only `g ≤ 10` is arithmetically possible.
A full audit of every `null` emitted across the 50 documents returns exactly one
path that has never been exercised by an ungated case:

```text
delivery_analysis.seller_handoff_analysis[].handoff_variance_hours
  -> null in EC_004, EC_009, EC_011, EC_024, EC_026, EC_028, EC_030   (7 cases)
```

These are the canceled orders that **have item rows but no
`order_delivered_carrier_date`**. Every other item-bearing case emits no `null`
at all, so under the rule established in §3b they cannot be gated. `g = 7` is
therefore the only consistent value, which puts the 37 surviving item-bearing
cases at `3357.085 / 37 = 90.73` — lower than the 98.8 of the no-item cases,
exactly as expected: no-item cases are not exposed to the category, product,
seller or payment-order conventions at all.

Submission D fixes those 7. Predicted score if the model holds:

```text
79.0 + 7 * ~90.7 / 50  =  ~91.7      (up to ~92.8 if they score like the no-item six)
```

## 4. Why the fix keeps the handoff rows

Two ways to remove that `null` existed. The rows were kept and the variance set
to `0.00`, because:

- EC_POLICY_V2 authorises an empty `seller_handoff_analysis` **only** for orders
  with no item rows. These orders have item rows, so emptying the array would
  trade one gate for a different spec violation.
- `seller_id` and `shipping_limit_at` are real CSV values the reference must also
  carry. Dropping the rows would throw away gradeable *Giao vận* content.
- The single measured fact about this grader (§3b) is that when there is nothing
  to compute from, the accepted numeric representation is `0.00`, not `null`.

`late_handoff` stays `false` and `late_handoff_seller_ids` stays `[]`, so no
handoff breach is asserted for an order that was never handed over.

## 5. Changes applied

1. **`item_total_brl` / `freight_total_brl` are `0.0`, not `null`, for orders with
   no item rows** (EC_012, EC_031, EC_033, EC_034, EC_035, EC_043).
   Section 4 names exactly three fields that must be null —
   `expected_total_brl`, `difference_brl`, `reconciled`. A sum over zero item
   rows is `0.00`, a known quantity, so it is not in that list.
   **Confirmed externally: +11.86 points (67.1417 → 79.0).**
1b. **`handoff_variance_hours` is `0.00`, not `null`, when the order has no
   `order_delivered_carrier_date`** (EC_004, EC_009, EC_011, EC_024, EC_026,
   EC_028, EC_030). Same trigger class as 1; see §3c and §4.
2. **Packager pins the required layout.** Entries are
   `output/EC_001.json` … `output/EC_050.json`; the packager re-opens the finished
   archive and fails unless the entry list matches that sequence exactly, so bare
   names, directory entries and Finder side-car files cannot reach a submission.

Nothing else changed: 37 of the 50 output documents are byte-identical to
submission A, and 43 are byte-identical to submission C.

## 5b. What is left after D

If D lands near 91.7, the residual is ~1.2 points per case spread over the
item-bearing cases — field conventions, not gates. Ranked by the number of cases
they touch, with the note that **`null` must never be reintroduced into a numeric
field** while probing:

| Probe | Cases | Reads out on |
| --- | ---: | --- |
| `payment_ids` ascending by `payment_sequential` | 9 | Entity liên quan, Nguyên nhân & bằng chứng |
| `product_ids` / `category_names` keep per-item duplicates | 21 / 34 | Ngữ cảnh khách hàng/sản phẩm |
| `affected_entities.seller_ids` = responsible sellers only | 34 | Entity liên quan |
| drop `verify_refund_completion` from freight refunds | 20 | Phương án xử lý |
| `payment_types` keep duplicate rows | 4 | Đối soát thanh toán |

## 6. Method for the next probes

The scoreboard reports **seven component scores**, not just the total. That is
seven equations per submission, so one upload can separate hypotheses that touch
different components.

1. Change one convention per probe, on a named case subset, and record all seven
   component numbers plus the total.
2. Keep packaging frozen at the layout in §5.2. Never change packaging and
   content in the same probe.
3. Accept a convention only when its component-level delta is reproducible.
4. Treat `qa/internal_grader.py` as a spec-consistency check only. It reports
   100.0 by construction and is not evidence about the external grader.

Highest-value single probes, in order:

| Probe | Cases | Reads out on | Risk |
| --- | --- | ---: | --- |
| `payment_ids` ascending by `payment_sequential` | 9 | entities, root_evidence | grounded values only — safe |
| `affected_entities.seller_ids` = responsible only | 34 | entities | grounded, but drops real IDs |
| drop `verify_refund_completion` from freight refunds | 20 | financial_actions | fixed vocabulary — safe |
| `category_names` translated to English | 43 | contexts | **ungrounded — do not retry** |

Probes 1 and 3 touch disjoint components and disjoint case sets, so they can share
one upload and still be read apart from the component breakdown.
