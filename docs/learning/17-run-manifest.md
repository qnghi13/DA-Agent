# 17. Run manifest: mỗi run biết mình do phiên bản agent nào tạo ra

Đây là phần đầu tiên của nhóm **harness**: những thứ quanh model giúp vận hành agent theo thời
gian, không phải tính năng cho người dùng.

## Khái niệm

Một agent không chỉ là code. Kết quả của nó phụ thuộc vào bốn thứ có thể đổi độc lập:

| Thành phần | Ví dụ thay đổi | Git sha có bắt được không? |
|---|---|---|
| Code | sửa verify, sửa tool | Có, nếu đã commit |
| Prompt và tool schema | thêm một câu vào system prompt | Có, nếu đã commit |
| Route model | đổi Groq sang vLLM, đổi seed | Không: nằm trong biến môi trường |
| Giới hạn | `max_iterations` 10 thành 8 | Không: nằm trong cấu hình |

**Manifest** gom cả bốn vào một tài liệu JSON. **Version id** là hash ngắn của tài liệu đó: cùng
cấu hình thì cùng id. Mỗi lần một job được chạy (attempt) ghi lại id này, span cũng mang id
(`gen_ai.agent.version` theo chuẩn OpenTelemetry GenAI), và eval ghi manifest vào `metrics.json`.
Khi điểm tụt hoặc một run sai, câu hỏi "phiên bản nào làm ra kết quả này?" có câu trả lời.

## Vì sao chọn như vậy

- **Hash thứ model nhìn thấy, không hash file nguồn.** Fingerprint của prompt lấy các hằng số viết
  hoa là dữ liệu thuần (prompt, tool spec, tên tool) cộng với văn bản do hàm sinh ra khi không có
  dữ liệu. Sửa comment không đổi phiên bản; thêm một câu vào prompt thì đổi. Git sha lo phần còn
  lại của code ([ADR 0038](../adr/0038-run-manifest.md)).
- **Cờ `dirty`.** Eval hay chạy trên code chưa commit. Lúc đó sha giống nhau mà kết quả khác, nên
  manifest ghi thêm `dirty: true` và fingerprint của prompt là thứ phân biệt hai lần chạy.
- **Ghi theo từng attempt, không theo job.** Một job có thể được worker khác nhận lại sau khi
  worker cũ chết, có khi sau một lần deploy. Bảng `job_attempts` có một dòng cho mỗi lần nhận.
- **Phiên bản nằm trong metadata của checkpoint.** LangGraph chép metadata của config vào mỗi
  checkpoint. Khi chạy tiếp, worker đọc phiên bản đã ghi checkpoint cuối và so với phiên bản của
  mình. Khác nhau thì attempt được đánh dấu `resumed_from_version`. Checkpoint cũ chưa có phiên bản
  là "không rõ", không phải "khác".
- **Không có bí mật trong manifest.** Route chỉ góp provider, model và tuỳ chọn request; test kiểm
  tra key không bao giờ xuất hiện.

## Phương án đã loại

- **Hash file nguồn:** đổi khi sửa comment hay refactor, nên phiên bản nhảy liên tục mà hành vi không
  đổi.
- **Hằng số version tăng bằng tay:** người sửa prompt quên tăng, và một phiên bản sai còn tệ hơn
  không có.
- **Mảng phiên bản trên bảng `jobs`, song song với `trace_ids`:** khi tắt tracing thì `trace_ids`
  không được thêm, hai mảng lệch nhau.
- **So với attempt trước thay vì checkpoint:** attempt trước có thể chết trước khi kịp ghi checkpoint.
- **Làm luôn chính sách khi phiên bản khác (chạy lại từ đầu, từ chối):** chưa biết chuyện này xảy ra
  bao nhiêu lần. Đánh dấu trước, đo, rồi mới quyết.

## Câu hỏi phỏng vấn

- **Điểm eval tụt 5 điểm sau một tuần. Bạn tìm nguyên nhân thế nào?** Nhóm kết quả theo version id,
  so manifest của hai phiên bản để biết phần nào đổi (code, prompt, route hay giới hạn), rồi mới đọc
  trace của các câu đổi kết quả. Không có version thì chỉ đoán được.
- **Deploy giữa lúc có run đang chạy dở thì sao?** Run tiếp tục từ checkpoint do code cũ ghi, với
  prompt mới. Ở đây attempt đó được đánh dấu; hệ thống chặt chẽ hơn có thể chạy lại từ đầu hoặc giữ
  worker cũ cho tới khi run cũ xong (giống cách Temporal dùng worker versioning).
- **Vì sao không dùng git sha là đủ?** Route model, seed và giới hạn nằm ngoài git, và eval thường
  chạy trên code chưa commit.
- **Prompt nên lưu trong code hay trong một prompt registry?** Registry (Langfuse, LangSmith) cho
  phép đổi prompt không cần deploy và gắn nhãn phiên bản. Dự án này giữ prompt trong code để review
  được trong PR; manifest cho cùng lợi ích truy vết mà không thêm dịch vụ.
