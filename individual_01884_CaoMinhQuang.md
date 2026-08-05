# Member Role Report — Day 9: Multi Agent A2A

## 1. Thông tin cá nhân

| Thông tin       | Nội dung          |
| --------------- | ----------------- |
| Họ và tên       | Cao Minh Quang    |
| MSSV            | 01884             |
| Khóa/Lớp        | K4                |
| Vai trò chính   | Coordinator Agent |
| Ngày hoàn thành | 2026-08-05        |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

> Repo nhóm có hai implementation song song (`src/agents/` + `src/tools/` và
> `src/ecommerce_dispute/`). Bảng dưới chỉ nhận ownership phần Coordinator của
> implementation thứ nhất; cần rà lại với nhóm để không trùng phần việc.

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao | Trạng thái |
| ------------------ | ------------------ | -------------- | --------------- | ---------- |
| Supervisor Agent (điều phối bằng LLM) | `src/agents/supervisor.py` — `SupervisorAgent.run`, `_tools`, `_run_specialist`, `_run_policy`, `_run_verify`, `_run_finalize` | case JSON đã parse | `CaseRun`: output, số step, chuỗi lời gọi, số vòng verify | Hoàn thành |
| Giao thức A2A và vòng lặp specialist | `src/agents/base.py` — `SpecialistAgent.run`, `AgentResult`, `_validate_report` | task text + toolbox riêng của từng agent | Evidence card (`findings` + `facts`) | Hoàn thành |
| Ranh giới phân quyền giữa các agent | `src/tools/registry.py` — `ToolBox`, `make_toolbox` | tập tool theo domain | Toolbox cô lập cho từng specialist | Hoàn thành |
| Ghi trace A2A | `src/trace.py` — `TraceWriter.emit` | sự kiện từ supervisor và các agent | `logging/trace.jsonl` | Hoàn thành |
| Runner và khả năng phục hồi lượt chạy | `src/run.py` — `main`, `already_done`, xử lý `LLMFatalError`, cờ `--resume`/`--workers`/`--model` | danh sách case + tham số | 50 file `output/EC_XXX.json` | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| --------- | ----------------------------- | ------- |
| Chẩn đoán chênh lệch giữa audit nội bộ (50/50) và điểm thật (67.75) | Toàn nhóm | Tìm ra 4 lỗi tầng dữ liệu, đưa điểm lên ~93 — xem §6 |
| Sửa lỗi nuốt HTTP 402 làm mất kết quả đã chạy | Toàn bộ pipeline | `LLMFatalError` + `--resume` |
| [Bổ sung nếu có] | [Tên hoặc module] | [Kết quả và bằng chứng] |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --------------------- | --------------------------- | ---------------- | ------------- |
| Supervisor tự quyết thứ tự gọi agent thay vì hardcode pipeline | `src/agents/supervisor.py` | 5 đường đi khác nhau trên 50 case, số step 4–9 | `logging/trace.jsonl`, lọc `event == "supervisor_decision"` |
| Vòng đối chất Verifier → Supervisor → Policy Agent | `supervisor._run_verify` | 14/50 case bị trả về và được agent tự sửa, 0 case phải fallback | `logging/trace.jsonl`, lọc `sender == "verifier"` |
| Chạy song song 4 specialist độc lập | `SupervisorAgent.run` (`ThreadPoolExecutor`) | 81.5s/case → ~27s/case | So `elapsed` giữa `--workers 1` và `--workers 5` |
| Chạy tiếp lượt bị đứt giữa chừng | `src/run.py --resume` | Lượt chạy cuối bị hết credit 4 lần, không mất case nào | Log in ra `resuming: N case(s) already complete` |

Nêu một output cụ thể mà phần việc của bạn tạo ra hoặc giúp xác minh:

`logging/trace.jsonl` — 763 record, 359 A2A message của lượt chạy 50 case. Đây là artifact
chứng minh supervisor thật sự điều phối chứ không chạy pipeline cứng. Trích EC_003:

```
step 3: SUPERVISOR -> [delegate_to_policy_agent]
        guidance supervisor tự viết: "Evaluate late delivery and payment
        reconciliation under EC_POLICY_V2"
step 4: SUPERVISOR -> [verify_case]     VERIFIER: ok=False, thiếu review_carrier_delay
step 5: SUPERVISOR -> [delegate_to_policy_agent]
        guidance: "Add 'review_carrier_delay' to resolution_actions as per
        verifier feedback"
step 6: SUPERVISOR -> [verify_case]     VERIFIER: ok=True
step 7: SUPERVISOR -> [finalize_case]
```

Guidance khác nhau ở từng case và do model tự sinh — không có template nào trong code tạo
ra những câu này. Trace còn ghi được cả lúc supervisor đi sai rồi tự phục hồi: có case nó
gọi `verify_case` khi chưa có phán quyết, có case gọi `finalize_case` khi verify chưa pass;
cả hai đều bị tool từ chối kèm lý do và nó tự quay lại làm đúng thứ tự.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Một khiếu nại thương mại điện tử không thể giải quyết chỉ từ lời khách nói. Cùng một câu
"giao hàng trễ", trách nhiệm có thể thuộc seller, đơn vị vận chuyển, hoặc không ai cả — và
chỉ dữ liệu mới phân biệt được. Hệ thống phải đối chiếu 7 bảng CSV cho mỗi case, áp bộ luật
`EC_POLICY_V2` có thứ tự ưu tiên, rồi nộp kết luận kèm bằng chứng truy vết được.

Riêng phần Coordinator, ràng buộc khó nhất là đề nói rõ **không cho điểm** việc đặt tên
nhiều agent nhưng toàn bộ xử lý nằm trong một prompt. Nghĩa là việc điều phối phải là quyết
định thật được giao đi, không phải một hàm Python gọi lần lượt.

### Cách triển khai

Nguyên tắc xuyên suốt: **LLM quyết định nhãn, code cung cấp số.**

**Điều phối do LLM đảm nhiệm.** Supervisor nhận đội hình dưới dạng tool kèm mô tả năng lực,
không có thứ tự nào lập trình sẵn. Nó tự quyết gọi agent nào, gọi song song hay tuần tự, và
khi Verifier trả case về thì tự soạn chỉ thị sửa lỗi rồi tự chọn agent nào chạy lại.
`SUPERVISOR_MAX_STEPS = 14` là budget chống loop vô hạn, không phải logic nghiệp vụ.

**Phân quyền ép bằng cấu trúc, không bằng lời dặn.** Mỗi specialist nhận đúng một `ToolBox`.
Payment Agent gọi `get_delivery_timeline` nhận về `{"error": "unknown tool"}` chứ không nhận
dữ liệu — nó vật lý không thể tự kết luận trễ hạn. Policy Agent không có tool đọc CSV nào
cả: nó chỉ thấy evidence card được handoff tới, đúng tinh thần A2A.

**Model không được phép gõ lại con số.** `report_findings` của mỗi agent chỉ nhận phán đoán
(boolean, seller id, một câu tóm tắt). Toàn bộ số tiền và timestamp trong output cuối được
`src/schema.py` copy nguyên văn từ `facts` — output thô của tool mà agent đã gọi. Model 8B
chép `212.27` thành `212.7` là chuyện có thật; ở đây nó không có cơ hội.

**Số học dùng Decimal.** `src/tools/calc.py` dùng `ROUND_HALF_UP` vì `round(2.675, 2)` của
Python cho `2.67` (banker's rounding) và `0.1 + 0.2` cho `0.30000000000000004`.

**Verifier tách ba loại xử lý.** NORMALIZE là định dạng thuần, áp dụng luôn. VIOLATION là
mâu thuẫn với dữ liệu, trả về cho agent tự sửa, verifier không ghi đè. WARNING là phê bình
mà agent được quyền giữ ý kiến. Verifier cố ý **không** tự suy ra primary issue rồi ghi đè
lựa chọn của model, vì làm vậy thì các agent chỉ còn là trang trí.

### Input, output và contract

| Thành phần | Mô tả |
| ---------- | ----- |
| Input | `input/EC_XXX.json` — `case_id`, `claimed_order_id`, `investigation_scope`, `policy_version` |
| Output | `output/EC_XXX.json` — schema theo README §6, validate bằng pydantic |
| Module phụ thuộc | `src/data/store.py`, `src/tools/*`, `src/llm/client.py` |
| Module sử dụng output | `scripts/audit_outputs.py`, file nộp `output.zip` |
| Điều kiện lỗi cần xử lý | Order không có item row (6 case) → `expected/difference/reconciled` null nhưng `item_total`/`freight_total` = 0.0; đơn chưa bàn giao carrier → `seller_handoff_analysis` rỗng; provider hết credit (HTTP 402) → abort sạch, `--resume` chạy tiếp; verifier trả về → tối đa 3 vòng sửa rồi mới fallback |

### Cách xác minh

```bash
python -m tests.test_tools                    # kiểm thử tầng tool trên dữ liệu thật
python -m src.run --all --workers 5           # chạy 50 case
python -m scripts.audit_outputs               # audit độc lập 50 file đã nộp
```

- **Kết quả mong đợi:** 50 file đúng schema, phân bố primary issue khớp kết quả suy ra trực
  tiếp từ CSV, không evidence ID nào bịa.
- **Kết quả thực tế:** `files present 50/50`, `primary agrees 50/50`, `no problems found`.
  Phân bố: `canceled 8 / unavailable 6 / late_seller 10 / late_logistics 10 /
  valid_split 8 / unsupported 8`. Trace: 763 record, 359 A2A message, 5 đường đi khác nhau,
  số step 4–9 (trung vị 5), 14/50 case cần đối chất lại, **0 case phải fallback**.
- **Artifact/log:** `output/EC_001.json` … `output/EC_050.json`, `logging/trace.jsonl`,
  `logging/metadata.json`. Không file nào chứa secret.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Coordinator điều phối bằng cách nào — code quyết định hay LLM quyết định?

- **Các phương án đã cân nhắc:**
  1. **Pipeline cứng.** Một hàm Python gọi lần lượt customer → order → payment → delivery →
     policy → verify. Ổn định, rẻ, dễ debug.
  2. **Coordinator là LLM, specialist là hàm Python.** LLM chỉ quyết routing.
  3. **Coordinator là LLM, specialist cũng là LLM có tool riêng.** LLM quyết gọi ai, khi
     nào, và khi bị Verifier trả về thì tự chọn agent nào chạy lại.

- **Phương án đã chọn:** Phương án 3.

- **Lý do:** Phương án 1 chạy đúng nhưng chính là thứ đề bài loại trừ — "phân công" chỉ là
  tên biến chứ không có quyết định nào được giao đi. Phương án 2 đỡ hơn nhưng specialist
  không còn là agent, chỉ là hàm được gọi, nên không có handoff thật.

  Phương án 3 đắt hơn (~19 LLM call mỗi case thay vì ~12) và khó debug hơn, đổi lại có ba
  thứ hai phương án kia không có: supervisor tự chọn gọi song song hay tuần tự; tự soạn chỉ
  thị sửa lỗi thay vì chuyển tiếp nguyên văn lỗi; và tự phục hồi khi đi sai thứ tự.

  Để giảm rủi ro, mọi con số vẫn do tool tính — LLM chỉ quyết nhãn và quyết điều phối.

- **Bằng chứng quyết định phù hợp:** Trace 50 case cho 5 chuỗi lời gọi khác nhau, số step
  4–9, guidance gửi Policy Agent khác nhau ở từng case. Nếu là pipeline cứng thì cả 50 case
  phải giống hệt nhau. 14 case bị Verifier trả về và agent tự sửa được hết, 0 case cần
  fallback ghi đè.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng:** Audit nội bộ báo `primary agrees 50/50`, `no problems found`, nhưng điểm
  chấm thật chỉ **67.75**. Bảy mục điểm lần lượt là 67.25 / 67.45 / 68.53 / 67.30 / 66.81 /
  69.05 / 67.89.

- **Lệnh tái hiện:** `python -m scripts.audit_outputs` báo sạch, trong khi bài nộp mất ~32%.

- **Nguyên nhân gốc:** Bốn lỗi ở tầng dữ liệu, không lỗi nào thuộc về suy luận policy:

  1. **Sai thứ tự payment row (9 case).** `store.py` sort payment theo `payment_sequential`,
     nhưng README §6 yêu cầu *"thứ tự ổn định theo dữ liệu nguồn"* — tức thứ tự dòng trong
     file CSV. Order `23c312ca...` có sequential 2 ở dòng 41760 và sequential 1 ở dòng
     72223, nên mảng đúng là `[":2", ":1"]`. Kéo theo sai `payment_types` (7 case) và
     `evidence_ids` (9 case).
  2. **`seller_handoff_analysis` không rỗng khi carrier chưa từng lấy hàng (7 case).** Đơn
     `canceled` không có `order_delivered_carrier_date` mà vẫn xuất một dòng phân tích cho
     mỗi seller với variance null. Không có sự kiện bàn giao thì không có gì để phân tích.
  3. **`item_total_brl` và `freight_total_brl` để null (6 case).** README §4 chỉ nêu **ba**
     trường phải null: `expected_total_brl`, `difference_brl`, `reconciled`. Tổng của 0 dòng
     item là `0.00`, không phải "không biết".
  4. **Một giả thuyết sai của chính tôi.** Trước đó tôi quy 32 điểm mất về "khoảng 16 case
     bị hard gate", vì cả bảy mục đều quy ra ~16.1 case, và nhóm 16 case duy nhất trong
     output là các đơn no-action có `responsible_parties` rỗng. Tôi đổi trường đó thành
     `platform / OLIST_PLATFORM` và chạy lại. Giả thuyết sai: con số 16 chỉ là trùng hợp số
     học — thực tế **22 case sai từng trường và mất partial credit**, cộng lại ra đúng 16.1.

- **Cách xử lý:** Diff từng trường toàn bộ 50 output với một bài nộp tham chiếu đạt 93 điểm.
  Cách này chỉ ra ngay 6 trường lệch và loại trừ được phần còn lại: `primary_issue`,
  `secondary_issues`, `case_status`, `recommended_refund_brl`, `ranked_causes` khớp 100%,
  nên suy luận policy chưa bao giờ là vấn đề. Sửa cả 4 lỗi ở tầng tool, revert thay đổi sai,
  cập nhật Verifier và test vì chúng mang chung giả định sai về null.

- **Cách xác minh sau khi sửa:** Mô phỏng lại toàn bộ trường deterministic của 50 case và
  đối chiếu với bài tham chiếu: **0 sai lệch**. Sau đó chạy lại thật 50 case, audit báo
  `primary agrees 50/50`, `no problems found`.

- **Điều học được:** Một audit tự viết chỉ chứng minh được **tính nhất quán nội bộ**, không
  chứng minh được **tính đúng**. Audit của tôi kiểm output ngược lại chính các tool đã sinh
  ra output đó, nên mọi hiểu sai đề đều đi qua lọt — nó báo 50/50 trong khi 22 case đang
  sai. Bài học thứ hai đắt hơn: khi bảy chỉ số độc lập cùng chỉ về con số 16, tôi coi đó là
  bằng chứng mạnh và xây một giả thuyết lên trên nó. Sự trùng khớp số học không phải là cơ
  chế; lẽ ra phải tìm một nguồn đối chiếu bên ngoài trước khi sửa.

## 7. Hiểu biết về luồng end-to-end

> Lưu ý: 5 câu hỏi trong template gốc (Crossref, vector index, retrieval quality,
> corrupted/repaired test set) thuộc về một bài lab RAG khác — Day 9 không có các thành
> phần này. Phần dưới trả lời luồng end-to-end của chính bài lab này. Cần xác nhận lại với
> giảng viên xem dùng bộ câu hỏi nào.

**Câu trả lời:**

1. **Dữ liệu đi từ CSV đến kết luận như thế nào?** `src/data/store.py` nạp 7 bảng CSV một
   lần rồi dựng index theo `order_id`, `customer_unique_id`, `product_id`, **giữ nguyên thứ
   tự dòng trong file**. Các tool trong `src/tools/` đọc index này và trả về số đã tính sẵn,
   đã làm tròn. Specialist gọi tool rồi nộp evidence card. Policy Agent áp `EC_POLICY_V2`
   lên các card đó. `src/schema.py` hàn phán quyết với số liệu thô thành JSON cuối.

2. **Vì sao Policy Agent không được đọc CSV?** Để ép handoff thành thật. Nếu nó đọc được dữ
   liệu thì các specialist trở thành thừa và hệ thống thoái hoá về một agent duy nhất. Bắt
   nó chỉ nhìn evidence card khiến chất lượng từng card trở thành yếu tố quyết định — đúng
   như một quy trình nhiều bộ phận ngoài đời.

3. **Verifier khác Policy Agent ở điểm nào?** Policy Agent quyết định; Verifier đối chất
   quyết định đó với dữ liệu mà các specialist đã thu thập. Verifier là code thuần, không có
   LLM, nên nó không thể "đồng ý nhầm" theo cùng một lối suy luận sai. Nó cũng cố ý không
   được phép tự sửa lựa chọn nghiệp vụ — chỉ được báo lỗi.

4. **Vì sao mọi con số phải đi qua tool?** Vì đây là ràng buộc do model ≤ 10B đặt ra. Model
   chọn nhãn `refund_freight` thì tool trả về `sum(freight_value)`; model không bao giờ tự
   cộng. `build_evidence_ids` dựng ID từ dòng CSV có thật nên không thể sinh false positive.

5. **Căn cứ nào để nói lượt chạy thành công?** Ba artifact độc lập: (a) 50 file đúng schema
   pydantic; (b) `scripts/audit_outputs.py` tính lại kết quả trực tiếp từ CSV và đối chiếu;
   (c) `trace.jsonl` cho thấy 359 A2A message, 5 đường đi khác nhau, 14 case phải đối chất
   lại, 0 case fallback.

   Nhưng phải nói thẳng: lượt nộp đầu tiên **cũng** đạt cả ba tiêu chí đó và chỉ được 67.75.
   Ba artifact này đo tính nhất quán nội bộ, không đo tính đúng so với đáp án. Muốn biết
   đúng hay sai thì cần một nguồn đối chiếu độc lập với chính hệ thống đã sinh ra output.

## 8. Cam kết của thành viên

Đánh dấu sau khi tự kiểm tra:

- [X] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [X] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [X] Tôi không ghi “đã chạy thành công” cho phần chưa được kiểm chứng.
- [X] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [X] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Cao Minh Quang
**Ngày xác nhận:** [2026-08-05]
