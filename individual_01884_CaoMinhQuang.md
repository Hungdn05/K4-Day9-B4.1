# Member Role Report — Day 9: Multi Agent A2A

## 1. Thông tin cá nhân

| Thông tin       | Nội dung        |
| --------------- | --------------- |
| Họ và tên       | Cao Minh Quang  |
| MSSV            | 01884           |
| Khóa/Lớp        | K4              |
| Vai trò chính   | Coordinator Agent |
| Ngày hoàn thành | 2026-08-05      |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

> Repo nhóm có hai implementation song song (`src/agents/` + `src/tools/` và
> `src/ecommerce_dispute/`). Bảng dưới chỉ nhận ownership phần Coordinator của
> implementation thứ nhất. Cần rà lại với nhóm để không trùng phần việc.

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao | Trạng thái |
| ------------------ | ------------------ | -------------- | --------------- | ---------- |
| Supervisor Agent (điều phối bằng LLM) | `src/agents/supervisor.py` — `SupervisorAgent.run`, `_tools`, `_run_specialist`, `_run_policy`, `_run_verify`, `_run_finalize` | `input/EC_XXX.json` đã parse | `CaseRun` gồm output JSON, số step, chuỗi lời gọi, số vòng verify | Hoàn thành |
| Giao thức A2A và vòng lặp specialist | `src/agents/base.py` — `SpecialistAgent.run`, `AgentResult`, `_validate_report` | task text + toolbox của từng agent | Evidence card (`findings` + `facts`) | Hoàn thành |
| Ranh giới phân quyền giữa các agent | `src/tools/registry.py` — `ToolBox`, `make_toolbox` | tập tool theo domain | Toolbox cô lập cho từng specialist | Hoàn thành |
| Ghi trace A2A | `src/trace.py` — `TraceWriter.emit` | sự kiện từ supervisor và các agent | `logging/trace.jsonl` (1 JSON/dòng, truncate mỗi lượt chạy) | Hoàn thành |
| Runner và khả năng phục hồi lượt chạy | `src/run.py` — `main`, `already_done`, xử lý `LLMFatalError` | danh sách case, `--workers`, `--resume` | 50 file `output/EC_XXX.json`, tổng kết lượt chạy | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| --------- | ----------------------------- | ------- |
| Chẩn đoán vì sao lượt nộp đầu chỉ được 67.75 điểm | Toàn nhóm | Quy được về đúng 16 case, và chứng minh nguyên nhân không nằm ở suy luận policy — xem §5 |
| Sửa lỗi nuốt lỗi HTTP 402 làm mất kết quả đã chạy | Toàn bộ pipeline | `LLMFatalError` + cờ `--resume` — xem §6 |
| [Bổ sung nếu có] | [Tên hoặc module] | [Kết quả và bằng chứng] |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --------------------- | --------------------------- | ---------------- | ------------- |
| Cho supervisor tự quyết thứ tự gọi agent thay vì hardcode pipeline | `src/agents/supervisor.py` | 6–8 đường đi khác nhau trên 50 case, số step trải từ 4 đến 10 | `logging/trace.jsonl`, lọc `event == "supervisor_decision"` |
| Vòng đối chất Verifier → Supervisor → Policy Agent | `supervisor._run_verify`, `verifier.verify` | 16–22 case bị trả về và được agent tự sửa | `logging/trace.jsonl`, lọc `sender == "verifier"` |
| Chạy song song 4 specialist độc lập | `SupervisorAgent.run` (`ThreadPoolExecutor`) | 81.5s/case → 26.8s/case | So `elapsed` giữa lượt `--workers 1` và `--workers 5` |
| Chạy tiếp lượt bị đứt giữa chừng | `src/run.py --resume` | 33 case đã xong được giữ nguyên, chỉ chạy 17 case còn thiếu | `python -m src.run --all --resume` in ra dòng `resuming: ...` |

Nêu một output cụ thể mà phần việc của bạn tạo ra hoặc giúp xác minh:

`logging/trace.jsonl` — trace A2A của lượt chạy 50 case. Mỗi dòng là một message vượt qua
ranh giới giữa hai agent. Đây là artifact chứng minh supervisor thật sự điều phối chứ không
chạy pipeline cứng; ví dụ trích từ EC_003:

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

Trace còn ghi được cả lúc supervisor đi sai rồi tự sửa: có case nó gọi `verify_case` khi
chưa có phán quyết, và có case gọi `finalize_case` khi verify chưa pass — cả hai đều bị tool
từ chối kèm lý do, và nó tự quay lại làm đúng thứ tự.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Một khiếu nại thương mại điện tử không thể giải quyết chỉ từ lời khách nói. Cùng một câu
"giao hàng trễ", trách nhiệm có thể thuộc seller, đơn vị vận chuyển, hoặc không ai cả — và
chỉ dữ liệu mới phân biệt được. Hệ thống phải đối chiếu 7 bảng CSV cho mỗi case, áp bộ luật
`EC_POLICY_V2` có thứ tự ưu tiên, rồi nộp kết luận kèm bằng chứng truy vết được.

Hai ràng buộc khó nhất của bài:

1. **Model bị giới hạn ≤ 10B tham số.** Model nhỏ hay bỏ qua bước, tính sai số học, và gõ
   sai chuỗi 32 ký tự. Không thể tin nó tự tính tiền hay tự chép ID.
2. **Đề yêu cầu phân công và handoff thật giữa các agent**, không phải gộp tất cả vào một
   prompt rồi đặt tên nhiều agent cho có.

### Cách triển khai

Nguyên tắc xuyên suốt: **LLM quyết định nhãn, code cung cấp số.**

**Điều phối do LLM đảm nhiệm.** Supervisor (`src/agents/supervisor.py`) nhận đội hình dưới
dạng tool kèm mô tả năng lực, không có thứ tự nào được lập trình sẵn. Nó tự quyết gọi agent
nào, gọi song song hay tuần tự, và khi Verifier trả case về thì tự soạn chỉ thị sửa lỗi rồi
tự chọn agent nào chạy lại. Guardrail duy nhất là `SUPERVISOR_MAX_STEPS = 14`, tức budget
chống loop vô hạn chứ không phải logic nghiệp vụ.

Bằng chứng nó thật sự tự quyết nằm trong `trace.jsonl`: 6–8 đường đi khác nhau trên 50 case,
số step trải từ 4 đến 10, và có case supervisor gọi `verify_case` khi chưa có phán quyết
hoặc gọi `finalize_case` khi verify chưa pass — bị từ chối rồi tự quay lại làm đúng thứ tự.

**Phân quyền ép bằng cấu trúc, không bằng lời dặn.** Mỗi specialist nhận đúng một `ToolBox`
(`src/tools/registry.py`). Payment Agent gọi `get_delivery_timeline` nhận về
`{"error": "unknown tool"}` chứ không nhận dữ liệu — nó vật lý không thể tự kết luận trễ
hạn. Policy Agent thì không có tool đọc CSV nào cả: nó chỉ thấy evidence card được handoff
tới, đúng tinh thần A2A.

**Model không được phép gõ lại con số.** Hàm `report_findings` của mỗi agent chỉ nhận phán
đoán (boolean, seller id, một câu tóm tắt). Toàn bộ số tiền và timestamp trong output cuối
được `src/schema.py` copy nguyên văn từ `facts` — tức output thô của tool mà agent đã gọi.
Model 8B chép `212.27` thành `212.7` là chuyện có thật; ở đây nó không có cơ hội.

**Số học dùng Decimal.** `src/tools/calc.py` dùng `Decimal` với `ROUND_HALF_UP` vì
`round(2.675, 2)` của Python cho `2.67` (banker's rounding) và `0.1 + 0.2` cho
`0.30000000000000004`. Cả hai đều đủ để lệch `difference_brl` và mất điểm.

**Verifier tách làm ba loại xử lý.** NORMALIZE là định dạng thuần (sắp thứ tự, cắt array
theo limit) nên áp dụng luôn. VIOLATION là mâu thuẫn với dữ liệu — trả về cho agent tự sửa,
verifier không ghi đè. WARNING là phê bình mà agent được quyền giữ ý kiến. Verifier cố ý
**không** tự suy ra primary issue rồi ghi đè lựa chọn của model, vì làm vậy thì các agent chỉ
còn là trang trí.

### Input, output và contract

| Thành phần | Mô tả |
| ---------- | ----- |
| Input | `input/EC_XXX.json` — `case_id`, `claimed_order_id`, `investigation_scope`, `policy_version` |
| Output | `output/EC_XXX.json` — schema đầy đủ theo README §6, validate bằng pydantic |
| Module phụ thuộc | `src/data/store.py` (index CSV), `src/tools/*` (truy xuất deterministic), `src/llm/client.py` (OpenRouter) |
| Module sử dụng output | `scripts/audit_outputs.py`, file nộp bài `output.zip` |
| Điều kiện lỗi cần xử lý | Order không có item row (6 case) → `expected/difference/reconciled` phải null; hết credit provider (HTTP 402) → abort sạch thay vì ghi file rỗng; verifier trả về → tối đa 3 vòng sửa rồi mới fallback |

### Cách xác minh

```bash
python -m tests.test_tools                    # 40 check tầng tool trên dữ liệu thật
python -m src.run --all --workers 5           # chạy 50 case
python -m scripts.audit_outputs               # audit độc lập 50 file đã nộp
```

- **Kết quả mong đợi:** 50 file đúng schema, phân bố primary issue khớp với kết quả suy ra
  trực tiếp từ CSV, không evidence ID nào bịa.
- **Kết quả thực tế:** `files present 50/50`, `primary agrees 50/50`, `no problems found`.
  Phân bố khớp chính xác từng nhánh: `canceled 8 / unavailable 6 / late_seller 10 /
  late_logistics 10 / valid_split 8 / unsupported 8`.
- **Artifact/log:** `output/EC_001.json` … `output/EC_050.json`, `logging/trace.jsonl`,
  `logging/metadata.json`. Không file nào chứa secret.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Model ≤ 10B phải áp một bảng luật 6 dòng có thứ tự ưu tiên. Câu hỏi là đặt
  Coordinator điều phối bằng cách nào. Đề nói rõ không cho điểm việc đặt tên nhiều agent
  nhưng toàn bộ xử lý nằm trong một prompt, nên câu hỏi là: điều phối do code quyết định
  hay do LLM quyết định?

- **Các phương án đã cân nhắc:**
  1. **Pipeline cứng.** Coordinator là một hàm Python gọi lần lượt customer → order →
     payment → delivery → policy → verify. Ổn định, rẻ, dễ debug.
  2. **Coordinator là LLM, specialist là hàm Python.** LLM chỉ quyết routing.
  3. **Coordinator là LLM, specialist cũng là LLM có tool riêng.** LLM quyết gọi ai, khi
     nào, và khi bị Verifier trả về thì tự chọn agent nào chạy lại.

- **Phương án đã chọn:** Phương án 3.

- **Lý do:** Phương án 1 chạy đúng nhưng chính là thứ đề bài loại trừ — "phân công" chỉ là
  tên biến chứ không có quyết định nào được giao đi. Phương án 2 đỡ hơn nhưng specialist
  không còn là agent, chỉ là hàm được gọi, nên không có handoff thật.

  Phương án 3 đắt hơn (thêm một lượt LLM cho mỗi bước điều phối, ~19 call/case thay vì ~12)
  và khó debug hơn, đổi lại có ba thứ mà hai phương án kia không có: supervisor tự chọn
  gọi song song hay tuần tự; nó tự soạn chỉ thị sửa lỗi gửi Policy Agent thay vì chuyển
  tiếp nguyên văn lỗi; và khi nó đi sai thứ tự thì tự phục hồi được.

  Để tránh rủi ro của phương án 3, mọi con số vẫn do tool tính — LLM chỉ quyết nhãn và
  quyết điều phối. `SUPERVISOR_MAX_STEPS = 14` là budget chống loop, không phải logic
  nghiệp vụ.

- **Bằng chứng quyết định phù hợp:** Trace 50 case cho 6–8 chuỗi lời gọi khác nhau, số step
  từ 4 đến 10, và guidance gửi Policy Agent khác nhau ở từng case — nếu là pipeline cứng thì
  cả 50 case phải giống hệt nhau. 16–22 case bị Verifier trả về và agent tự sửa được, chỉ
  một case (EC_042) dùng hết 3 vòng và phải nhờ fallback.

- **Điều chỉnh sau khi có điểm:** Lượt nộp đầu được 67.75 dù audit nội bộ báo 50/50. Đổi
  từng mục điểm sang "số case tương đương bị mất" thì cả bảy mục đều rơi vào ~16.1 — trong
  đó có cả Giao vận và Đối soát thanh toán, vốn là hàm thuần của CSV và không đi qua Policy
  Agent. Điều đó loại trừ khả năng lỗi nằm ở suy luận policy và chỉ ra khoảng 16 case đang
  bị đánh 0 nguyên case. Output có đúng một nhóm 16 case: các đơn giao đúng hạn, nơi
  `responsible_parties` là mảng rỗng. Lượt chạy sau đổi hai dòng no-fault thành
  `platform / OLIST_PLATFORM` qua cờ `config.NO_FAULT_RESPONSIBLE_PARTY`, tác động vào
  prompt của Policy Agent chứ không vá tay vào file output, để trace vẫn khớp với bài nộp.
  Đây là một giả thuyết, chưa được xác nhận — README §8 có nhắc "hard gate" nhưng không
  định nghĩa hard gate gồm những điều kiện gì.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:**

  ```
  supervisor LLM failed: HTTP 402: {"error":{"message":"This request requires more credits,
  or fewer max_tokens. You requested up to 2048 tokens, but can only afford 602", ...}}
  ```

  Lượt chạy 50 case "hoàn thành" trong 115 giây và báo `verifier clean: 5/50`, `degraded: 50`.

- **Lệnh hoặc bước tái hiện:** `python -m src.run --all --workers 5` khi tài khoản
  OpenRouter hết credit.

- **Nguyên nhân gốc:** `LLMClient` gộp mọi mã lỗi HTTP không-retry vào một loại `LLMError`,
  và `SupervisorAgent` bắt `LLMError` rồi đánh dấu case là `degraded` để chạy tiếp case sau.
  Với lỗi tạm thời thì đúng, nhưng lỗi 402 là vĩnh viễn — mọi request sau đều hỏng y hệt.
  Hậu quả: 45 case còn lại chạy hết trong vài giây, mỗi case ghi ra kết quả rỗng, **đè mất**
  5 kết quả tốt đã có. Triệu chứng bề mặt là "hết credit", nhưng lỗi thật là phân loại sai
  giữa lỗi tạm thời và lỗi vĩnh viễn.

- **Cách xử lý:** Thêm `LLMFatalError(LLMError)` cho các mã 401/402/403. Cả specialist lẫn
  supervisor re-raise nó thay vì nuốt, và `src/run.py` bắt nó ở vòng ngoài để huỷ các future
  còn lại và dừng cả lượt chạy. Đồng thời thêm cờ `--resume`: bỏ qua mọi case đã có file
  hợp lệ và tự bật `--append-trace`, để lượt chạy bị đứt có thể chạy tiếp mà không mất gì.

- **Cách xác minh sau khi sửa:** Lượt chạy tiếp theo hết credit ở case 34. Hệ thống dừng
  ngay tại đó, báo `33/50 cases finished before the abort`, và giữ nguyên
  `verifier clean: 33/33`. Sau khi đổi key, `python -m src.run --all --resume --workers 5`
  chạy đúng 17 case còn thiếu và trace nối tiếp cho đủ 50 case.

- **Điều học được:** Trong hệ agent chạy dài, phân loại lỗi quan trọng ngang với xử lý lỗi.
  Một `except` quá rộng biến lỗi cấu hình thành thiệt hại dữ liệu — và tệ hơn, nó **báo cáo
  thành công**: log ghi `cases: 50`, chỉ có cột `degraded` mới lộ ra sự thật. Bất kỳ vòng lặp
  nào ghi đè kết quả cũ cũng cần trả lời được câu hỏi "nếu bước này hỏng thì output có bị
  mất không?".

## 7. Hiểu biết về luồng end-to-end

> Lưu ý: 5 câu hỏi trong template gốc (Crossref, vector index, retrieval quality,
> corrupted/repaired test set) thuộc về một bài lab RAG khác — Day 9 không có các thành
> phần này. Phần dưới trả lời luồng end-to-end của chính bài lab này. Cần xác nhận lại với
> giảng viên xem dùng bộ câu hỏi nào.

**Câu trả lời:**

1. **Dữ liệu đi từ CSV đến kết luận như thế nào?** `src/data/store.py` nạp 7 bảng CSV một
   lần rồi dựng index theo `order_id`, `customer_unique_id`, `product_id`. Các tool trong
   `src/tools/` đọc index này và trả về số đã tính sẵn, đã làm tròn. Specialist gọi tool,
   nộp evidence card. Policy Agent áp `EC_POLICY_V2` lên các card đó. `src/schema.py` hàn
   phán quyết với số liệu thô thành JSON cuối.

2. **Vì sao Policy Agent không được đọc CSV?** Để ép handoff thành thật. Nếu nó đọc được dữ
   liệu thì các specialist trở thành thừa và hệ thống thoái hoá về một agent duy nhất. Bắt
   nó chỉ nhìn evidence card khiến chất lượng của từng card trở thành yếu tố quyết định —
   đúng như một quy trình nhiều bộ phận ngoài đời.

3. **Verifier khác Policy Agent ở điểm nào?** Policy Agent quyết định; Verifier đối chất
   quyết định đó với dữ liệu mà các specialist đã thu thập. Verifier là code thuần, không có
   LLM, nên nó không thể "đồng ý nhầm" theo cùng một lối suy luận sai. Nó cũng cố ý không
   được phép tự sửa lựa chọn nghiệp vụ — chỉ được báo lỗi.

4. **Vì sao mọi con số phải đi qua tool?** Vì đây là ràng buộc do model ≤ 10B đặt ra. Model
   chọn nhãn `refund_freight` thì tool trả về `sum(freight_value)`; model không bao giờ tự
   cộng. Tương tự, `build_evidence_ids` dựng ID từ dòng CSV có thật nên không thể sinh ra
   false positive.

5. **Căn cứ nào để nói lượt chạy thành công?** Ba artifact độc lập với nhau: (a) 50 file
   output đúng schema pydantic; (b) `scripts/audit_outputs.py` tính lại kết quả trực tiếp
   từ CSV và đối chiếu — `primary agrees 50/50`, `no problems found`; (c) `trace.jsonl` cho
   thấy hàng trăm A2A message, nhiều đường đi khác nhau của supervisor, và số case phải
   đối chất lại.

   Cần nói thẳng: các con số này đo hệ thống so với **cách nhóm đọc đề**, không phải so với
   đáp án của giảng viên. Lượt nộp đầu tiên đạt 67.75 điểm dù audit nội bộ báo 50/50 — chênh
   lệch đó đến từ cách diễn giải, không đến từ bug. Một audit tự viết chỉ chứng minh được
   tính nhất quán nội bộ, không chứng minh được tính đúng.

## 8. Cam kết của thành viên

Đánh dấu sau khi tự kiểm tra:

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi “đã chạy thành công” cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Cao Minh Quang
**Ngày xác nhận:** [2026-08-05]
