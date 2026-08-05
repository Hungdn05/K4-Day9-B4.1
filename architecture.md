# Kiến trúc Multi-Agent — K4 Day 09

Hệ thống điều tra 50 khiếu nại thương mại điện tử trên dữ liệu Olist bằng một đội agent
có phân công, handoff và kiểm chứng lẫn nhau.

- **Model**: `qwen/qwen3-8b` (8B tham số, ≤ 10B theo README §9.1) qua OpenRouter
- **Framework**: Python thuần, không dùng LangChain/CrewAI — vòng lặp tool-calling tự viết
- **Điều phối**: LLM supervisor, không có pipeline cứng

---

## 1. Sơ đồ agent

```
                          ┌──────────────────────────┐
   input/EC_XXX.json ───► │   SUPERVISOR (LLM)       │
                          │   qwen3-8b + reasoning   │
                          │   agent-as-tool          │
                          └────────────┬─────────────┘
                    tự quyết gọi ai, khi nào, có gọi lại không
        ┌──────────────┬──────────────┼──────────────┬───────────────┐
        ▼              ▼              ▼              ▼               ▼
 ┌────────────┐ ┌────────────┐ ┌───────────┐ ┌────────────┐  ┌─────────────┐
 │  CUSTOMER  │ │   ORDER &  │ │  PAYMENT  │ │  DELIVERY  │  │   POLICY    │
 │   AGENT    │ │  PRODUCT   │ │   AGENT   │ │   AGENT    │  │   AGENT     │
 │            │ │   AGENT    │ │           │ │            │  │ + reasoning │
 └─────┬──────┘ └─────┬──────┘ └─────┬─────┘ └─────┬──────┘  └──────┬──────┘
       │              │              │             │                │
   CUSTOMER_      ORDER_TOOLS   PAYMENT_TOOLS  DELIVERY_TOOLS   POLICY_TOOLS
    TOOLS                                                            │
       └──────────────┴──────────────┴─────────────┘                 │
                    4 agent dữ liệu chạy song song                   │
                    (độc lập, không cái nào cần output cái nào)       │
                                     │                               │
                          evidence card (A2A message)                │
                                     └───────────┬───────────────────┘
                                                 ▼
                                     ┌──────────────────────┐
                                     │  VERIFIER (thuần code)│
                                     │  normalize/violation/ │
                                     │  warning              │
                                     └──────────┬────────────┘
                                    violation ──┘ trả về supervisor
                                    supervisor tự chọn agent chạy lại
                                                 │
                                          finalize_case
                                                 ▼
                                        output/EC_XXX.json
                                        logging/trace.jsonl
```

---

## 2. Vai trò và quyền truy cập

Quyền truy cập được **ép bằng cấu trúc**, không phải bằng lời dặn trong prompt: mỗi agent
nhận đúng một `ToolBox` (`src/tools/registry.py`). Gọi tool ngoài domain trả về
`{"error": "unknown tool ..."}` chứ không trả dữ liệu.

| Agent | File | Toolbox | Đọc được | Nộp về |
|---|---|---|---|---|
| **Supervisor** | `src/agents/supervisor.py` | agent-as-tool | không đọc CSV | điều phối, quyết định đóng case |
| **Customer** | `src/agents/specialists.py` | `CUSTOMER_TOOLS` | customers, orders (theo `customer_unique_id`) | `is_repeat_customer`, số order liên quan |
| **Order & Product** | `src/agents/specialists.py` | `ORDER_TOOLS` | orders, order_items, products, category translation | `order_status`, `has_item_rows`, các cờ multi-* |
| **Payment** | `src/agents/specialists.py` | `PAYMENT_TOOLS` | order_payments, order_items (để đối soát) | `payment_row_count`, `reconciliation_status` |
| **Delivery** | `src/agents/specialists.py` | `DELIVERY_TOOLS` | orders (timestamp), order_items (`shipping_limit_date`) | `delivered_late`, `late_handoff_seller_ids` |
| **Policy** | `src/agents/policy_agent.py` | `POLICY_TOOLS` | **không đọc CSV** — chỉ thấy evidence card | primary/secondary issue, root cause, bên chịu trách nhiệm, actions |
| **Verifier** | `src/agents/verifier.py` | không có LLM | facts + output | violations / warnings / normalizations |

Ba nhóm không giao nhau là chủ ý: Payment Agent không thấy được timestamp giao hàng nên
không thể tự ý kết luận trễ hạn; Delivery Agent không thấy tiền nên không thể tự ý đề xuất
hoàn; Policy Agent không thấy CSV nên buộc phải ra quyết định dựa trên bằng chứng được
handoff tới, đúng tinh thần A2A.

---

## 3. Luồng handoff

1. **Supervisor nhận case.** Được trao đội hình dưới dạng tool kèm mô tả năng lực. Không có
   thứ tự nào được lập trình sẵn — nó tự lập kế hoạch.
2. **Dispatch specialist.** Supervisor chọn gọi ai. Khi nó phát nhiều lời gọi specialist dữ
   liệu trong cùng một lượt, hệ thống chạy chúng đồng thời (4 agent này không chia sẻ state,
   đọc toolbox rời nhau — song song hoá đổi đồng hồ chứ không đổi kết quả).
3. **Evidence card.** Mỗi specialist trả về `AgentResult` gồm `findings` (phán đoán) và
   `facts` (output nguyên văn của tool). Supervisor chỉ nhìn `findings` để giữ context gọn.
4. **Policy Agent ra phán quyết.** Nhận case brief do code dựng từ các evidence card — không
   agent trung gian nào được diễn giải lại con số. Bật `reasoning` vì đọc bảng ưu tiên
   top-down là bước model nhỏ hay bỏ qua.
5. **Verifier đối chất.** Kiểm tra phán quyết ngược lại với facts. Trả về violations
   (phải sửa) và warnings (nên cân nhắc).
6. **Supervisor xử lý phản hồi.** Đọc từng finding, tự soạn guidance và tự chọn agent nào
   chạy lại. Bảng `LIKELY_OWNER` chỉ là gợi ý, supervisor có quyền bỏ qua.
7. **finalize_case.** Chỉ thành công khi case vượt qua verification.

---

## 4. Nguyên tắc chống ảo giác

Ranh giới xuyên suốt hệ thống: **LLM quyết định nhãn, code cung cấp số.**

| Thứ | Nguồn |
|---|---|
| Gọi agent nào, thứ tự nào, retry ra sao | LLM supervisor |
| Primary/secondary issue, root cause, bên chịu trách nhiệm | LLM policy agent |
| Mọi số tiền, số giờ, timestamp | tool pandas (`src/tools/`) |
| Mọi evidence ID | `build_evidence_ids` dựng từ dòng CSV có thật |
| Số tiền hoàn | `compute_refund_amount` theo basis mà LLM chọn |

`report_findings` của mỗi agent chỉ nhận **phán đoán** (boolean, id, summary). Số liệu trong
output cuối được `src/schema.py` copy nguyên văn từ `facts` — model không có cơ hội gõ lại
`212.27` thành `212.7`.

Mọi phép tính tiền và giờ dùng `Decimal` với `ROUND_HALF_UP` (`src/tools/calc.py`), vì
`round(2.675, 2)` của Python cho `2.67` và `0.1 + 0.2` cho `0.30000000000000004`.

---

## 5. Verifier: ba loại xử lý tách bạch

| Loại | Xử lý | Ví dụ |
|---|---|---|
| **NORMALIZE** | Áp dụng luôn, thuần định dạng | sắp thứ tự `secondary_issues`, cắt array theo limit, `case_status` theo refund |
| **VIOLATION** | Trả về supervisor, **verifier không ghi đè** | `late_delivery_seller` nhưng không seller nào trễ handoff |
| **WARNING** | Phê bình, agent được quyền giữ ý kiến | "đơn này canceled và đã trả tiền, đã cân nhắc dòng 1 chưa?" |

Verifier **không** tự suy ra primary issue rồi ghi đè lựa chọn của model. Chỉ sau khi
supervisor dùng hết `VERIFIER_MAX_REPAIR_ROUNDS = 3` vòng sửa mới có fallback ghi lại các
trường thuần cơ học (`secondary_issues`, `resolution_actions`, refund), và mỗi lần fallback
đều được ghi vào trace.

---

## 6. Cấu trúc mã nguồn

```
src/
  config.py              model name, budget, limit, đường dẫn
  policy.py              taxonomy EC_POLICY_V2 (chỉ tên + thứ tự, không có logic quyết định)
  schema.py              pydantic contract + build_output (hàn facts với decision)
  trace.py               ghi A2A trace
  run.py                 CLI
  llm/client.py          OpenRouter client, tool-calling, retry, đếm token
  data/store.py          load CSV + index
  tools/
    registry.py          Tool/ToolBox — ranh giới phân quyền
    calc.py              Decimal + timestamp
    customer_tools.py  order_tools.py  payment_tools.py
    delivery_tools.py  policy_tools.py
  agents/
    base.py              vòng lặp specialist
    specialists.py       4 agent dữ liệu
    policy_agent.py      agent phán quyết
    verifier.py          kiểm chứng deterministic
    supervisor.py        điều phối LLM
tests/test_tools.py      kiểm thử tầng tool trên dữ liệu thật
scripts/                 harness debug (không nằm trên đường chạy chính)
```

## 7. Chạy

```bash
cp .env.example .env       # điền OPENROUTER_API_KEY
python -m tests.test_tools # kiểm thử tầng tool
python -m src.run --all --workers 5
```
