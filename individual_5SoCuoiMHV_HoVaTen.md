# Báo cáo cá nhân — Day 9: Multi-Agent A2A

> Đây là project làm solo. Các mục có nhãn [TỰ ĐIỀN] là thông tin cá nhân hoặc thông tin triển khai không thể xác định chắc chắn chỉ từ repository.

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
| --- | --- |
| Họ và tên | [TỰ ĐIỀN: họ và tên] |
| MSSV | [TỰ ĐIỀN: mã số sinh viên] |
| Khóa/Lớp | [TỰ ĐIỀN: khóa/lớp] |
| Vai trò chính | Tác giả duy nhất: phân tích, thiết kế, lập trình, kiểm thử và đóng gói toàn bộ pipeline |
| Ngày hoàn thành | [TỰ ĐIỀN: ngày hoàn thành hoặc ngày nộp thực tế] |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

Vì project được thực hiện solo, tôi sở hữu toàn bộ phần triển khai hiện có. Các phần chính
được chia thành các module sau:

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao | Trạng thái |
| --- | --- | --- | --- | --- |
| Kiểm tra input và dữ liệu Olist | src/multiagent_a2a/data/cases.py, src/multiagent_a2a/data/olist.py — load_cases(), OlistRepository.load_for_orders() | 50 file input/EC_*.json và 4 CSV: orders, items, payments, sellers | CaseInput bất biến, repository đã lọc theo order, kiểm tra schema/khóa/foreign key/timestamp | Hoàn thành, đã kiểm chứng |
| Structured handoff và orchestration | src/multiagent_a2a/contracts.py, src/multiagent_a2a/ports.py, src/multiagent_a2a/agents/coordinator.py | Case hợp lệ và các read-port dữ liệu | Handoff giữa Coordinator, Order/Seller, Payment, Delivery, Policy và Verifier | Hoàn thành, đã kiểm chứng |
| Phân tích domain và quyết định canonical | agents/order_seller.py, agents/payment.py, agents/delivery.py, domain/policy.py, agents/policy.py | Trạng thái order, item/seller, payment và timestamp giao hàng | PolicyDecision và JSON kết quả theo EC_POLICY_V1 | Hoàn thành, đã kiểm chứng |
| Verifier, QA và artifact | agents/verifier.py, artifacts/qa.py, artifacts/json_io.py, application/pipeline.py | Candidate output, canonical output và evidence allowlist | 50 JSON, submission.zip, QA report, trace và metadata | Hoàn thành, đã kiểm chứng |
| Backend Qwen offline và notebook standalone | llm/qwen.py, llm/parsing.py, multi_agent_ecommerce_dispute_qwen3.ipynb | Proposal tối giản từ handoff; model local tùy chọn | Proposal Qwen nếu model local hợp lệ, hoặc deterministic fallback; notebook tự chứa pipeline | Fallback đã kiểm chứng; Qwen inference thực tế và Kaggle Run All cần tự xác nhận |

### Việc hỗ trợ ngoài phạm vi chính

Không có thành viên khác trong project solo. Tôi tự thực hiện phần tích hợp giữa hai
entrypoint, tài liệu hóa kiến trúc và hướng dẫn chạy:

| Hoạt động | Module được tích hợp | Kết quả |
| --- | --- | --- |
| Đồng bộ package modular và notebook standalone | src/multiagent_a2a/, multi_agent_ecommerce_dispute_qwen3.ipynb, README.md, PROJECT_GUIDE.md, architecture.md | Hai đường chạy cùng dùng policy/output contract; notebook có golden payload SHA-256 và tự kiểm tra ZIP |
| Regression cho lỗi confidence và dữ liệu timestamp | tests/test_data_and_pipeline.py, tests/test_verifier.py, tests/test_domain_policy.py | Thêm kiểm tra profile confidence 0.92, verifier repair và từ chối timestamp bị mất độ chính xác |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --- | --- | --- | --- |
| Xây dựng pipeline xử lý toàn bộ 50 case | application/pipeline.py, agents/*, output/ | 50 output JSON; 32 case action_required, 18 case no_action; trace 502 event | pytest -q; python -m multiagent_a2a validate ... |
| Khóa output theo contract chấm điểm | domain/policy.py, constants.py, agents/verifier.py, artifacts/qa.py | 50/50 case có confidence=0.92; issue counts và tổng tiền khớp golden profile | QA trong metadata.json; test regression |
| Đảm bảo không nhân đôi tiền và không suy diễn evidence | data/olist.py, agents/order_seller.py, agents/payment.py, domain/policy.py | Item/payment được aggregate riêng; evidence chỉ lấy từ order, item, payment, seller và policy code | tests/test_data_and_pipeline.py, tests/test_domain_policy.py |
| Đóng gói artifact nộp bài | artifacts/qa.py, submission.zip, metadata.json, trace.jsonl | ZIP có đúng 50 JSON ở root; payload SHA-256 là d6f9007649523a48f11bde5e98a0166ebdbb87d6aadb92275f7e205d550e2e78 | CLI validate, kiểm tra danh sách ZIP và metadata |

Một output cụ thể mà phần việc tạo ra là output/EC_001.json. Output này chứa issue
late_delivery_seller, cause SELLER_HANDOFF_AFTER_LIMIT, evidence có thể dựng lại từ
CSV và refund freight 12.04 BRL. Artifact tổng hợp hiện ghi nhận:

- Issue counts: late_delivery_seller=8, late_delivery_logistics=8,
  canceled_order_paid=8, unavailable_order_paid=8,
  valid_split_payment=9, unsupported_late_claim=9.
- Tổng tiền: item 4686.52 BRL, freight 727.47 BRL, payment 7782.89 BRL,
  recommended refund 3429.64 BRL.
- Backend: Qwen/Qwen3-8B, kích thước 8.2B, nhưng lượt chạy hiện tại dùng
  disabled_deterministic_fallback; 50/50 case được xử lý bằng rule engine.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Hệ thống phải điều tra 50 khiếu nại thương mại điện tử từ nhiều bảng Olist, xác định
đúng issue chính, bên chịu trách nhiệm, evidence, số tiền hoàn và action. Kết quả phải
reproducible, không được tạo evidence hoặc sự kiện không tồn tại trong CSV, đồng thời
phải chạy được khi không có GPU, model hoặc network.

### Cách triển khai

load_cases() chỉ chấp nhận đúng bộ file EC_001.json đến EC_050.json, kiểm tra
exact schema, duplicate JSON key, trùng case/order và policy_version. Repository
đọc bốn bảng chính sách cần thiết, kiểm tra cột bắt buộc, uniqueness, foreign key,
seller ID và độ chính xác timestamp; sau đó lọc theo 50 order được yêu cầu. Item và
payment được aggregate riêng, tránh join nhiều-nhiều làm nhân đôi tiền.

Coordinator điều phối theo thứ tự:

1. OrderSellerAgent lấy trạng thái order, item, seller, shipping limit, item total và freight total.
2. PaymentAgent cộng từng payment row đúng một lần và đối soát với item + freight trong sai số 0.10 BRL.
3. DeliveryAgent so sánh delivered date với estimated date, đồng thời kiểm tra carrier có bàn giao sau shipping limit của seller hay không. Timestamp thiếu được xử lý theo hướng fail closed.
4. PolicyAgent tính quyết định canonical bằng DeterministicPolicyEngine theo đúng thứ tự ưu tiên của EC_POLICY_V1. Qwen chỉ nhận handoff tối giản để đề xuất issue/cause/party; proposal chỉ được chấp nhận khi khớp quyết định deterministic.
5. VerifierAgent kiểm tra schema, enum, giới hạn số lượng, tiền, status, action, evidence allowlist và profile confidence. Candidate sai được thay bằng canonical output.
6. Pipeline ghi JSON nguyên tử, chạy QA golden, tạo ZIP chỉ chứa 50 JSON ở root, rồi ghi trace và metadata.

### Input, output và contract

| Thành phần | Mô tả |
| --- | --- |
| Input | input/EC_001.json … input/EC_050.json; olist_orders_dataset.csv, olist_order_items_dataset.csv, olist_order_payments_dataset.csv, olist_sellers_dataset.csv; policy EC_POLICY_V1 |
| Output | output/EC_*.json theo schema README; submission.zip; trace.jsonl/logging/trace.jsonl; metadata.json/logging/metadata.json |
| Module phụ thuộc | contracts.py, ports.py, data/, domain/, llm/, observability/ |
| Module sử dụng output | application/pipeline.py, artifacts/qa.py, CLI validate, notebook standalone |
| Điều kiện lỗi cần xử lý | Thiếu/thừa file, schema sai, duplicate key, order/seller/payment không tồn tại, timestamp bị rewrite, policy ngoài coverage, model local không hợp lệ và QA/ZIP mismatch. Lỗi dữ liệu và QA là hard-fail; lỗi backend model mới được fallback. |

### Cách xác minh

~~~powershell
$env:PYTHONPATH = "src"
pytest -q
~~~

~~~powershell
$env:PYTHONPATH = "src"
python -m multiagent_a2a validate --data-dir data --input-dir input --output-dir output --zip-path submission.zip
~~~

- **Kết quả mong đợi:** test pass; validate xác nhận đủ 50 case, issue/status/tổng tiền đúng golden profile, confidence đồng nhất 0.92 và ZIP chỉ có JSON ở root.
- **Kết quả thực tế:** 28 passed in 11.90s; validate pass với output_count=50, confidence_counts={"0.92": 50}, action_required=32, no_action=18, tổng refund 3429.64 BRL. ZIP có 50 entry từ EC_001.json đến EC_050.json.
- **Artifact/log:** output/, submission.zip, metadata.json, trace.jsonl, logging/metadata.json, logging/trace.jsonl. Không ghi API key, token hoặc secret vào báo cáo.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** LLM có thể đề xuất issue/cause/party nhưng không nên tự quyết định số tiền, evidence hoặc kết luận cuối cùng. Nếu phụ thuộc hoàn toàn vào model, output có thể không ổn định, không chạy được khi thiếu model và có thể tạo evidence không có trong dữ liệu.
- **Các phương án đã cân nhắc:**
  1. Để Qwen sinh trực tiếp toàn bộ output cuối cùng.
  2. Dùng rule engine deterministic làm nguồn thẩm quyền; Qwen chỉ tạo proposal nhỏ, chỉ nhận proposal khớp canonical, còn lại fallback.
  3. Tắt hoàn toàn LLM và chỉ dùng rule engine trong mọi môi trường.
- **Phương án đã chọn:** Phương án 2, đồng thời cho phép chạy phương án 3 bằng --no-llm hoặc khi model local không sẵn sàng.
- **Lý do:** Cách này giữ correctness và provenance của tiền/evidence, reproducibility của bài chấm và khả năng chạy offline; vẫn giữ được boundary multi-agent và khả năng dùng Qwen local khi có asset hợp lệ. Chi phí inference cũng không làm hỏng pipeline.
- **Bằng chứng quyết định phù hợp:** Lượt chạy hiện tại xử lý đủ 50 case với qwen_validated_cases=0, deterministic_fallback_cases=50, QA pass, 502 trace event và payload SHA-256 khớp golden. Các test offline cũng kiểm tra gateway không import model runtime khi bị disable và chỉ cho phép local_files_only=True.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi:** Audit được ghi trong PROJECT_GUIDE.md cho thấy bản trước đạt khoảng 94.11/100 (xấp xỉ 16/17 nhóm field), trong đó lỗi có tính hệ thống là confidence=0.99 ở cả 50 output trong khi payload tham chiếu yêu cầu 0.92. Đây là mismatch chấm điểm, không phải stack trace runtime; repository không lưu secret hoặc log lỗi có secret.
- **Lệnh hoặc bước tái hiện:** So sánh assessment.confidence của 50 file trong output/ với reference profile; chạy regression test của pipeline và verifier. Trước khi sửa, fixture/test contract còn kỳ vọng 0.99; sau khi sửa, profile chính thức được kiểm tra là {"0.92": 50}.
- **Nguyên nhân gốc:** Confidence cũ được hard-code thành 0.99, trong khi QA chỉ aggregate issue/status/tổng tiền và verifier chưa coi confidence chính thức là một phần của contract.
- **Cách xử lý:** Đổi sang constant OFFICIAL_CONFIDENCE = 0.92; dùng constant trong assemble_output(); thêm confidence_policy_mismatch vào verifier; thêm kiểm tra kiểu, miền [0,1] và profile confidence vào artifact QA/metadata; cập nhật notebook và test regression.
- **Cách xác minh sau khi sửa:** pytest -q cho 28 passed; CLI validate pass với confidence_counts={"0.92": 50}; ZIP có đúng 50 JSON; metadata ghi payload SHA-256 d6f9007649523a48f11bde5e98a0166ebdbb87d6aadb92275f7e205d550e2e78.
- **Điều học được:** Mọi field có trọng số trong output đều phải được coi là contract và đưa vào golden QA; không nên chỉ kiểm tra các field nghiệp vụ lớn mà bỏ qua một scalar lặp lại trên toàn bộ test set.

Ngoài blocker trên, tôi bổ sung kiểm tra timestamp để từ chối dữ liệu bị spreadsheet rewrite
như 1/1/2018 0:00, vì việc mất giây có thể làm thay đổi so sánh deadline. Trường hợp này
được kiểm tra bằng test test_olist_loader_rejects_timestamp_precision_lost_by_spreadsheet.

## 7. Hiểu biết về luồng end-to-end

Các câu hỏi Crossref/vector index trong mẫu không khớp với project này: repo không dùng
Crossref, embedding hoặc vector database. Đây là pipeline phân tích rule-based trên dữ liệu
Olist, có optional local Qwen.

1. **Dữ liệu đi từ Crossref đến vector index như thế nào?**
   Không áp dụng. Dữ liệu thực tế đi từ 50 case JSON và bốn CSV Olist vào load_cases() và
   OlistRepository. Repository lọc theo claimed_order_id, sau đó các agent tạo handoff
   có cấu trúc; không có bước chunking, embedding hay vector retrieval.

2. **Evaluation set và ground-truth document IDs dùng để đo retrieval/answer quality ra sao?**
   Không có document ID ground truth hoặc retrieval evaluation set trong bài này. Evaluation
   set là 50 case chính thức EC_001–EC_050; chất lượng được đối chiếu bằng policy canonical
   dựng từ CSV, issue/status counts, tổng tiền, confidence profile và payload SHA-256.

3. **Quality checks khác freshness monitoring ở điểm nào trong bài lab?**
   Quality checks kiểm tra ngay trong lượt chạy: schema, evidence allowlist, enum, giới hạn,
   số tiền, status, số lượng output, golden counts/totals, hash và ZIP. Freshness monitoring
   sẽ theo dõi tuổi/cập nhật của nguồn dữ liệu theo thời gian; project không có thành phần
   freshness monitoring. Kiểm tra timestamp ở loader chỉ bảo vệ data integrity, không phải
   theo dõi độ mới.

4. **Vì sao phải dùng cùng test set cho baseline, corrupted và repaired?**
   Project hiện không lưu ba dataset baseline/corrupted/repaired riêng. Nguyên tắc vẫn là
   dùng cùng 50 case để tách ảnh hưởng của thay đổi thuật toán khỏi thay đổi dữ liệu. Trong
   repo, cùng bộ 50 case được dùng để chạy output, kiểm tra canonical và validate artifact.

5. **Repair được xem là thành công dựa trên artifact và metric nào?**
   Verifier repair thành công khi candidate lỗi được thay bằng canonical output, không còn
   lỗi schema/business/evidence và output qua QA. Bằng chứng là các test repair trong
   tests/test_verifier.py, 50 output hợp lệ, confidence_counts={"0.92": 50}, issue/status
   counts, aggregate totals, ZIP 50 entry và payload SHA-256 khớp golden.

## 8. Cam kết của thành viên

Tôi sẽ tự kiểm tra và đánh dấu trước khi nộp:

- [ ] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [ ] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [ ] Tôi không ghi “đã chạy thành công” cho phần chưa được kiểm chứng.
- [ ] Báo cáo không chứa .env, API key, token hoặc secret.
- [ ] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** [TỰ ĐIỀN: họ và tên]<br>
**Ngày xác nhận:** [TỰ ĐIỀN: ngày xác nhận thực tế]
