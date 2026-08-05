# Member Role Report — Day 9: Multi Agent A2A

## 1. Thông tin cá nhân

| Thông tin       | Nội dung                                      |
| --------------- | --------------------------------------------- |
| Họ và tên       | **CẦN BỔ SUNG HỌ VÀ TÊN**                    |
| MSSV            | **CẦN BỔ SUNG MSSV**                          |
| Khóa/Lớp        | K4                                            |
| Vai trò chính   | Coordinator, Policy, Integration & Verification |
| Ngày hoàn thành | 2026-08-05                                    |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao | Trạng thái |
| ------------------ | ------------------ | -------------- | ----------------- | ---------- |
| Data contract và repository | `contracts.py`, `data_loader.py`, `repository.py` | 9 CSV Olist và 50 input JSON | Bản ghi đã validate và index theo khóa join | Hoàn thành |
| Agent orchestration | `pipeline.py::DisputeCoordinator` | `CaseRequest`, repository | 7 handoff/case và output draft | Hoàn thành |
| Policy và verification | `policy.py`, `contracts.py::validate_case_output` | `CaseFacts`, output draft | Issue, responsibility, refund, actions và validation | Hoàn thành |
| Batch và artifact nộp bài | `batch.py::run_batch` | 50 case đã validate | 50 output, trace, metadata, `output.zip` | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| --------- | ----------------------------- | ------- |
| Tích hợp domain handoff | Customer, Order/Product, Payment, Delivery | Chuẩn hóa về `CaseFacts`, giữ thứ tự dữ liệu ổn định |
| Kiểm thử dữ liệu thật | Policy và Verifier | Phủ đủ 6 primary issue bằng order Olist đại diện |
| Tài liệu kiến trúc | Toàn pipeline | Hoàn thành `architecture.md` với quyền truy cập và luồng handoff |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --------------------- | --------------------------- | ---------------- | ------------- |
| Đối soát và phân loại 50 case | `output/EC_001.json` … `EC_050.json` | 50/50 output đúng envelope và semantic rule | Batch verifier và parse lại toàn bộ JSON |
| Ghi trace chạy thật | `logging/trace.jsonl` | 350 agent handoff và 350 model review, 7 event/case | Parse 350 dòng JSONL và response ID |
| Ghi cấu hình chạy | `logging/metadata.json` | Model, policy, framework, runtime, distribution | Kiểm tra JSON metadata |
| Đóng gói bài nộp | `output.zip` | Đúng 50 JSON, không có file lạ | `ZipFile.testzip()` và kiểm tra danh sách entry |
| Kiểm thử | `tests/` | 8/8 test pass, phủ đủ 6 primary issue | `python3 -m unittest discover` |

Artifact chính là `output.zip` gồm đúng 50 file từ `EC_001.json` đến
`EC_050.json`. Batch phân loại được 8 canceled, 6 unavailable, 10 seller-late,
10 logistics-late, 8 valid split payment và 8 unsupported late claim.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Phần việc giải quyết việc biến một order ID trong khiếu nại thành kết luận có
thể kiểm chứng. Pipeline phải join đúng nhiều bảng Olist, không tin nội dung
khiếu nại như bằng chứng, xử lý order có nhiều item/seller/payment và trả về
JSON đúng schema, giới hạn mảng và thứ tự policy.

### Cách triển khai

Repository nạp một lần các bảng cần thiết và tạo index theo `order_id`,
`customer_id`, `customer_unique_id` và `product_id`. Bốn domain worker chạy
song song trên cấu trúc chỉ đọc:

1. Customer worker tìm khách duy nhất và các order lịch sử.
2. Order/Product worker lấy item, seller, product và category theo thứ tự nguồn.
3. Payment worker dùng `Decimal`, cộng item + freight và đối soát sai số 0.10 BRL.
4. Delivery worker tính chênh lệch giờ và shipping limit sớm nhất theo seller.

Coordinator gom handoff thành `CaseFacts`. Policy engine áp dụng 6 primary rule
theo đúng precedence, sau đó thêm secondary issue và action theo thứ tự nghiệp
vụ. Verifier dựng lại evidence ID từ record nguồn, kiểm tra schema, null handling
và các giới hạn trước khi ghi file.

### Input, output và contract

| Thành phần              | Mô tả |
| ----------------------- | ----- |
| Input                   | `EC_001..EC_050.json`, `claimed_order_id`, `EC_POLICY_V2`, 9 CSV Olist |
| Output                  | 11 section JSON/case, trace JSONL, metadata JSON và ZIP |
| Module phụ thuộc        | `data_loader.py`, `repository.py`, `handoffs.py`, `policy.py` |
| Module sử dụng output   | Verifier, batch runner và hệ thống chấm |
| Điều kiện lỗi cần xử lý | Thiếu order/customer/product, sai schema, thiếu item, timestamp null, payment lệch, evidence giả, vượt array cap |

### Cách xác minh

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m ecommerce_dispute batch --root . --archive output.zip --use-model --model-workers 3
```

- **Kết quả mong đợi:** 8 test pass; 50 output; ZIP đúng 50 entry; trace và metadata hợp lệ.
- **Kết quả thực tế:** 8/8 test pass; 50/50 output; 350 trace/model event, 81.501 token; ZIP test không lỗi.
- **Artifact/log:** `output/`, `output.zip`, `logging/trace.jsonl`, `logging/metadata.json`.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** LLM có thể diễn giải khiếu nại nhưng không nên tự quyết định số tiền, timestamp hoặc evidence.
- **Các phương án đã cân nhắc:** (1) để model tự đọc CSV và sinh toàn bộ JSON; (2) dùng agent handoff nhưng tính toán và policy bằng Python xác định.
- **Phương án đã chọn:** Agent handoff kết hợp deterministic joins, `Decimal`, timestamp arithmetic và policy engine.
- **Lý do:** Giảm hallucination, giữ số tiền chính xác, chạy lặp lại được và audit tới từng record nguồn. `gpt-4o-mini` được khai báo theo cấu hình dự án; số 8B là xác nhận của project owner, không phải số OpenAI công bố.
- **Bằng chứng quyết định phù hợp:** 50/50 case qua validator; đủ 6 nhánh policy; 350 model invocation được ghi response ID/usage; không có evidence ID sai định dạng; model review không có quyền thay đổi số liệu.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** `Input directory must contain exactly EC_001..EC_050` và báo thiếu toàn bộ 50 file.
- **Lệnh hoặc bước tái hiện:** `PYTHONPATH=src python3 -m ecommerce_dispute check-inputs --input-dir input`.
- **Nguyên nhân gốc:** Starter repo ban đầu chỉ có `input/.gitkeep`; bộ case riêng của lab không thuộc Kaggle Olist.
- **Cách xử lý:** Bổ sung đúng 50 input JSON, thêm preflight filename/case ID và chỉ cho batch chạy khi đủ bộ.
- **Cách xác minh sau khi sửa:** Preflight đọc đủ 50 case; batch trả `output_count=50`, không có failure.
- **Điều học được:** Cần tách data source khỏi evaluation input và fail sớm trước khi chạy pipeline hoặc ghi artifact dở dang.

## 7. Hiểu biết về luồng end-to-end

Giải thích ngắn gọn bằng lời của bạn:

1. Một case đi từ input đến output qua các agent như thế nào?
2. Customer history được tách khỏi affected entities ra sao?
3. Payment và delivery được đối soát bằng công thức nào?
4. Evidence và verifier ngăn hallucination như thế nào?
5. Một batch thành công được xác nhận bằng artifact và metric nào?

**Câu trả lời:**

1. Input được validate, order ID được dùng để truy vấn repository, bốn domain
   worker tạo handoff, coordinator chuẩn hóa facts, policy quyết định và verifier
   duyệt draft trước khi ghi output.
2. `customer_id` gắn với một order, còn `customer_unique_id` nhận diện cùng khách
   qua nhiều order. Chỉ claimed order nằm trong `affected_entities`; lịch sử được
   giới hạn trong `customer_context.related_order_ids`.
3. Payment dùng tổng `price + freight`, so với tổng payment và reconciled khi
   chênh lệch tuyệt đối không quá 0.10 BRL. Delivery dùng delivered minus
   estimated; seller handoff dùng carrier timestamp minus shipping limit sớm
   nhất của từng seller. Tất cả làm tròn hai chữ số.
4. Evidence chỉ chấp nhận các prefix và ID dựng trực tiếp từ order/item/payment/
   seller cùng policy code. Verifier tái dựng danh sách kỳ vọng và từ chối ID
   thừa, sai format hoặc không khớp output entities.
5. Batch thành công khi có đúng 50 output hợp lệ, trace parse được 350 event có
   model response ID, metadata ghi đủ runtime/model/policy/usage, ZIP chứa đúng
   50 tên file và CRC không lỗi.

## 8. Cam kết của thành viên

Đánh dấu sau khi tự kiểm tra:

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi “đã chạy thành công” cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** **CẦN BỔ SUNG HỌ VÀ TÊN**
**Ngày xác nhận:** 2026-08-05
