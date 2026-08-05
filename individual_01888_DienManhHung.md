# Member Role Report — Day 9: Multi Agent A2A

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
| --- | --- |
| Họ và tên | Điền Mạnh Hùng |
| MSSV | 01888 |
| Khóa/Lớp | K4 |
| Vai trò chính | Coordinator, Policy, Integration & Verification |
| Ngày hoàn thành | 2026-08-05 |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao | Trạng thái |
| --- | --- | --- | --- | --- |
| Data contract và repository | `contracts.py`, `data_loader.py`, `repository.py` | 9 CSV Olist và 50 input JSON | Dữ liệu đã preflight, validate và index theo khóa join | Hoàn thành |
| Điều phối các agent | `pipeline.py::DisputeCoordinator`, `handoffs.py` | `CaseRequest`, repository chỉ đọc | 7 typed handoff/case và output draft | Hoàn thành |
| Policy và verification | `policy.py::decide_policy`, `contracts.py::validate_case_output` | `CaseFacts`, output draft | Issue, trách nhiệm, refund, actions và kết quả validation | Hoàn thành |
| Batch và artifact nộp bài | `batch.py::run_batch`, `batch.py::create_submission_archive` | 50 case hợp lệ | 50 output JSON, trace, metadata và `SUBMISSION.zip` | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| --- | --- | --- |
| Tích hợp domain handoff | Customer, Order/Product, Payment và Delivery worker | Chuẩn hóa dữ liệu từ bốn domain thành `CaseFacts`, giữ ranh giới trách nhiệm giữa các agent |
| Kiểm thử dữ liệu thật | Policy và Verifier | Test bằng order Olist đại diện cho đủ 6 nhánh primary issue |
| Debug regression điểm chấm | Pipeline, internal checker và packaging | Xác định mutation đồng thời trên 50 case gây hard-gate; khôi phục các semantics bám dữ liệu nguồn và chuyển sang kiểm thử từng giả thuyết độc lập |
| Tài liệu kiến trúc | Toàn pipeline | Hoàn thành `architecture.md` về quyền truy cập, typed handoff và luồng kiểm chứng |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --- | --- | --- | --- |
| Đối soát và phân loại 50 case | `output/EC_001.json` … `output/EC_050.json` | 50/50 output qua schema validator | Chạy batch và parse lại toàn bộ JSON |
| Ghi trace handoff | `logging/trace.jsonl` | 350 event, tương ứng 7 handoff cho mỗi case | `wc -l logging/trace.jsonl` trả về `350` |
| Ghi cấu hình chạy | `logging/metadata.json` | Model, policy, framework, runtime và phân bố primary issue | Parse bằng `jq`; không lưu API key |
| Đóng gói bài nộp | `SUBMISSION.zip` | Đúng 50 entry `output/EC_001.json` đến `output/EC_050.json` | Kiểm tra danh sách entry và CRC bằng `unzip` |
| Kiểm thử | `tests/` | 12/12 test pass, có test dữ liệu thật cho đủ 6 primary issue | `PYTHONPATH=src python3 -m unittest discover -s tests -v` |
| Phân tích hard gate từ điểm external | `qa/internal_grader_findings.md` | Xác định cơ chế gate và định lượng từng nhóm case | Trừ điểm theo component breakdown, xem mục 6 |

Artifact đầu ra hiện tại là `SUBMISSION.zip`, gồm đúng 50 JSON và có SHA-256
`e7583d27e62c458cb271d8e577e49964875271ded89f68f12faa490b8e0cfbd4`.
Batch phân loại được 8 case canceled, 6 unavailable, 10 seller-late, 10
logistics-late, 8 valid split payment và 8 unsupported late claim.

Lượt recovery hiện tại chạy deterministic, tạo 350 handoff và không gọi model
(`invocation_count = 0`). Một lượt audit trước đó đã được kiểm chứng trong commit
`ffafdd3`, dùng `gpt-4o-mini` cho 350 handoff với tổng 107.267 token. Hai loại
run được phân biệt rõ để không ghi khống model usage cho artifact hiện tại.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Phần việc của tôi biến một `claimed_order_id` trong khiếu nại thành kết luận có
thể kiểm chứng trên dữ liệu Olist. Pipeline phải join đúng nhiều bảng, xử lý
order có nhiều item, seller hoặc payment, áp dụng `EC_POLICY_V2` theo precedence
và xuất JSON đúng schema. Nội dung khiếu nại không được dùng thay cho bằng chứng
trong CSV.

### Cách triển khai

Repository nạp dữ liệu một lần rồi tạo các index theo `order_id`, `customer_id`,
`customer_unique_id` và `product_id`. Coordinator chạy song song bốn worker độc
lập trên dữ liệu chỉ đọc:

1. Customer worker xác định `customer_unique_id` và các order lịch sử.
2. Order/Product worker lấy item, seller, product và category theo thứ tự nguồn.
3. Payment worker dùng `Decimal` để cộng payment, item, freight và đối soát với
   ngưỡng sai số 0,10 BRL.
4. Delivery worker tính chênh lệch giao hàng và chênh lệch seller handoff theo
   timestamp trong CSV.

Coordinator gom các handoff thành `CaseFacts`. Policy engine áp dụng sáu primary
rule đúng thứ tự ưu tiên, sau đó thêm secondary issue và action theo thứ tự nghiệp
vụ. Verifier dựng lại evidence từ ID nguồn, kiểm tra schema, null handling, giới
hạn mảng và từ chối evidence không thể truy ngược.

Các phép join, số tiền, timestamp và policy đều deterministic. Adapter OpenAI
chỉ audit nội dung handoff khi bật `--use-model`; phản hồi model được ghi trace
nhưng không có quyền sửa fact, refund hoặc evidence.

### Nguyên tắc không tin claim, chỉ tin dữ liệu đã join và verify

Đây là ràng buộc thiết kế trung tâm, và nó được bảo đảm **bằng cấu trúc chứ không
bằng quy ước**:

1. **Message khiếu nại không bao giờ trở thành fact.** `contracts.py` parse và
   validate `customer_request.message`, nhưng trường này **không được truyền vào**
   `pipeline.py`. Coordinator chỉ dùng `request.claimed_order_id` để truy vấn và
   `request.case_id` để gắn nhãn output. Không một field nào trong 11 section của
   output được sinh ra từ nội dung khiếu nại, nên khách hàng không thể "khai" ra
   một sự kiện giao trễ, giao thiếu hay hoàn tiền.
2. **Phải join đủ nguồn trước khi kết luận.** Mỗi case bắt buộc đi qua bốn worker
   trên bốn domain: `orders`+`customers` (identity, lịch sử), `order_items`+
   `products` (item, seller, product, category), `order_payments` (đối soát tiền),
   và timestamp giao vận. Policy engine nhận `CaseFacts` đã chuẩn hóa, không được
   đọc CSV thô, nên không thể kết luận khi thiếu nguồn.
3. **Verify trước khi ghi, không phải sau.** `investigate()` gọi `_verify_evidence`
   dựng lại toàn bộ `evidence_ids` từ các record đã thu thập rồi `validate_case_output`
   kiểm schema, cap mảng và null handling; chỉ khi cả hai pass mới trả draft, và
   `run_batch` validate lần nữa **trước** `_write_json`. Verifier là stage riêng nên
   không agent nào tự phê duyệt kết luận của mình.
4. **Claim sai bị bác bỏ tường minh.** Khi timestamp chứng minh đơn giao trong hạn
   và payment khớp, policy trả `unsupported_late_claim` với refund `0`, action
   `reject_late_refund` và `responsible_parties` rỗng — tức hệ thống bác claim thay
   vì làm theo yêu cầu hoàn tiền.

Ràng buộc 1 và 4 được khóa bằng test `test_complaint_text_can_never_become_a_fact`:
cùng một order, một request khai "giao trễ 10 ngày, yêu cầu hoàn toàn bộ tiền" và
một request chỉ hỏi trung lập cho ra output **giống hệt nhau**, và kết quả là
`unsupported_late_claim` / `no_action` / refund `0.0`.

### Input, output và contract

| Thành phần | Mô tả |
| --- | --- |
| Input | `input/EC_001.json` … `EC_050.json`, `claimed_order_id`, `EC_POLICY_V2` và 9 CSV Olist |
| Output | JSON gồm 11 section cho mỗi case; 7 handoff/case; trace JSONL; metadata JSON; submission ZIP |
| Module phụ thuộc | `data_loader.py`, `repository.py`, `handoffs.py`, `policy.py`, `contracts.py` |
| Module sử dụng output | Verifier, batch runner, submission packager và hệ thống chấm |
| Điều kiện lỗi cần xử lý | Thiếu order/customer/product, thiếu đủ 50 input, timestamp null, order không có item, payment lệch, evidence sai, mảng vượt cap hoặc output sai schema |

### Cách xác minh

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m ecommerce_dispute batch --root . --archive SUBMISSION.zip
unzip -Z1 SUBMISSION.zip | wc -l
unzip -tq SUBMISSION.zip
```

- **Kết quả mong đợi:** 12 test pass; 50 output; 350 trace event; ZIP có 50 entry `output/EC_XXX.json` và CRC hợp lệ.
- **Kết quả thực tế:** 12/12 test pass; batch xử lý 50/50 case; trace có 350 dòng; `unzip` đếm 50 entry và báo `No errors detected in compressed data`.
- **Artifact/log:** `output/`, `SUBMISSION.zip`, `logging/trace.jsonl`, `logging/metadata.json`; không có secret trong các artifact này.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** LLM có thể hỗ trợ diễn giải handoff nhưng không nên tự quyết định số tiền, timestamp, trách nhiệm hoặc evidence.
- **Các phương án đã cân nhắc:** (1) Cho model đọc toàn bộ CSV và tự sinh output; (2) dùng một prompt lớn chứa toàn bộ pipeline; (3) tách domain agent, tính toán và policy bằng Python deterministic, model chỉ audit handoff.
- **Phương án đã chọn:** Typed domain handoff kết hợp deterministic joins, `Decimal`, timestamp arithmetic, policy engine và verifier độc lập; OpenAI audit là lớp tùy chọn, không phải source of truth.
- **Lý do:** Phương án này giảm hallucination, giữ số tiền chính xác, dễ tái hiện, tiết kiệm token và cho phép truy vết từng kết luận về record nguồn.
- **Bằng chứng quyết định phù hợp:** 50/50 case qua validator, test dữ liệu thật phủ đủ 6 nhánh policy, trace có đúng 7 event/case và verifier không chấp nhận evidence sai định dạng. Lượt model audit trước đó cũng không được phép ghi đè dữ liệu deterministic.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** Điểm external giảm từ `67.1417` xuống `5.4654` sau một lượt tối ưu semantic; sau khi khôi phục, điểm vẫn dừng ở `67.1417` dù output khớp từng giá trị với ví dụ mẫu trong README.
- **Lệnh hoặc bước tái hiện:** So sánh `git diff` của 50 output trước và sau mutation; đếm số case bị tác động theo từng field; chạy lại internal consistency checker và test suite; dựng oracle tham số hóa 13 convention và tính điểm của output hiện tại trên toàn bộ 8.192 tổ hợp.
- **Nguyên nhân gốc:** Hai lỗi phương pháp và một lỗi dữ liệu.
  1. Internal checker bị overfit theo một điểm tổng và dùng lại đúng các giả thuyết vừa áp dụng vào production. Năm convention bị đổi cùng lúc nên không còn control case.
  2. Sai convention **không thể** giải thích khoảng cách 33 điểm: quét 8.192 tổ hợp cho thấy điểm thấp nhất đạt được chỉ là `85.27`. Suy ra phải có case bị hard gate về 0, không phải mất điểm rải rác.
  3. Lỗi dữ liệu thật: `item_total_brl` và `freight_total_brl` được ghi `null` cho 6 order không có item row. README chỉ nêu đúng ba field phải null (`expected_total_brl`, `difference_brl`, `reconciled`); tổng của 0 dòng item là `0.00`, là đại lượng đã biết.
- **Cách xử lý:** Đổi hai field đó thành `0.0` cho đúng 6 case (`EC_012`, `EC_031`, `EC_033`, `EC_034`, `EC_035`, `EC_043`) và **không đổi gì khác**, để delta external chỉ thuộc về một giả thuyết duy nhất.
- **Cách xác minh sau khi sửa:** Điểm external tăng `67.1417 → 79.1415`. Quy đổi:

  ```text
  (79.1415 - 67.1417) * 50 / 6 = 99.998 điểm mỗi case
  ```

  Sáu case đó đi từ 0 lên đúng 100. Ba kết luận kiểm chứng được: hard gate là thật; một case đúng hoàn toàn được đúng 100 điểm nên 100 là khả thi; và luật gate **không phải** "cấm null" — chính 6 case đó vẫn giữ `delivered_at`, `carrier_handoff_at`, `delivery_variance_hours`, `expected_total_brl`, `difference_brl`, `reconciled` là `null` mà vẫn full điểm. Luật khớp dữ liệu là: *case bị 0 khi một field là `null` trong khi reference có số ở đó*.
- **Điều học được:** Không thể suy nhiều quy ước ẩn từ một aggregate score, và không được dùng oracle có cùng giả định với production để tự chứng minh correctness. Ngược lại, một thay đổi cô lập trên một nhóm case đã nêu tên biến bảng điểm thành dụng cụ đo: chia delta cho số case là ra điểm/case, và component breakdown cho bảy phương trình mỗi lượt nộp thay vì một.

Blocker external chưa đóng hoàn toàn. Sau khi đã trừ 6 case nói trên, 44 case còn
lại vẫn chỉ đạt trung bình ~76,3 và mất điểm **đều nhau** trên cả sáu component
chính (77,17 / 77,39 / 76,39 / 76,70 / 77,29 / 76,71), tức là dấu hiệu của case bị
0 chứ không phải sai field. Chặn trên tính từ component `entities` cho biết nhiều
nhất ~10 case còn bị gate, mỗi case trị giá 2,0 điểm tổng.

Probe thứ hai đã loại được một giả thuyết: đổi `handoff_variance_hours` từ `null`
thành `0.00` cho 7 order canceled không có `order_delivered_carrier_date` cho ra
điểm **giống hệt** `79.1415`. Vì thay đổi bên trong một case bị gate là vô hình,
kết luận là 7 case đó bị gate bởi chính **sự tồn tại của row handoff**, không phải
bởi giá trị null. Bản hiện tại vì vậy ghi `seller_handoff_analysis: []` khi không
có sự kiện bàn giao trong dữ liệu, đúng với yêu cầu không tự tạo sự kiện không tồn
tại của đề bài. Lượt nộp này chưa được chấm nên tôi chưa tuyên bố đã đóng.

## 7. Hiểu biết về luồng end-to-end

1. Một case đi từ input đến output qua các agent như thế nào?
2. Customer history được tách khỏi affected entities ra sao?
3. Payment và delivery được đối soát bằng công thức nào?
4. Evidence và verifier ngăn hallucination như thế nào?
5. Một batch thành công được xác nhận bằng artifact và metric nào?

**Câu trả lời:**

1. Input được validate trước, sau đó `claimed_order_id` được dùng để truy vấn
   repository. Bốn domain worker tạo typed handoff; coordinator chuẩn hóa thành
   `CaseFacts`; policy quyết định issue, trách nhiệm, refund và actions; verifier
   duyệt output trước khi batch ghi JSON.
2. `customer_id` gắn với một order, còn `customer_unique_id` nhận diện cùng khách
   hàng qua nhiều order. Chỉ claimed order được đưa vào `affected_entities`;
   order lịch sử chỉ xuất hiện trong `customer_context.related_order_ids`.
3. Payment total là tổng các `payment_value`; expected total là tổng `price` cộng
   tổng `freight_value`; `reconciled` đúng khi trị tuyệt đối của chênh lệch không
   quá 0,10 BRL. Delivery variance bằng delivered trừ estimated; seller handoff
   variance bằng carrier handoff trừ shipping limit sớm nhất của seller. Tiền và
   số giờ được làm tròn hai chữ số.
4. Evidence chỉ dùng các ID dựng được từ order, item, payment, seller chịu trách
   nhiệm và policy code. Verifier tái dựng danh sách evidence kỳ vọng, kiểm tra
   format, giới hạn mảng và source grounding; vì vậy nội dung model hoặc customer
   message không thể tự tạo thêm sự kiện.
5. Batch thành công khi đủ 50 output hợp lệ, đủ 350 handoff, metadata ghi đúng
   model/policy/runtime/run, test suite pass và ZIP chứa đúng 50 tên file với CRC
   hợp lệ. Nếu bật model audit thì metadata còn phải phản ánh đúng invocation và
   token usage; không được ghi có model call cho run deterministic.

## 8. Cam kết của thành viên

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi “đã chạy thành công” cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Điền Mạnh Hùng  
**Ngày xác nhận:** 2026-08-05
