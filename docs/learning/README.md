# Ghi chú học

Mỗi cột mốc có một ghi chú tiếng Việt, theo khung sau:

1. **Khái niệm:** vấn đề là gì, giải thích bằng lời đơn giản.
2. **Vì sao chọn như vậy:** liên hệ với code hoặc ADR tương ứng.
3. **Phương án đã loại:** và lý do loại.
4. **Câu hỏi phỏng vấn:** câu hỏi có thể gặp, kèm ý trả lời.

| Ghi chú | Cột mốc |
|---|---|
| [01. Nền móng kiến trúc](01-nen-mong-kien-truc.md) | Thiết kế trước khi viết code |
| [02. Dữ liệu, sandbox và đo lường](02-du-lieu-sandbox-va-do-luong.md) | Ingestion, SQL trong sandbox, baseline đầu tiên |
| [03. Graph và checkpoint](03-graph-va-checkpoint.md) | LangGraph, chạy tiếp sau sự cố, verify |
| [04. Kiểm tra câu trả lời và độ tin cậy](04-kiem-tra-va-do-tin-cay.md) | Verify bằng code, test lỗi và injection |
| [05. Biểu đồ, báo cáo và truy nguồn con số](05-bieu-do-va-bao-cao.md) | Vega-Lite do code dựng, báo cáo HTML, cắt RAG |
| [06. Backend: hàng đợi, worker, API và giới hạn](06-backend-va-hang-doi.md) | Queue trên Postgres, worker, API, SSE, rate và budget |
| [07. Giao diện React](07-giao-dien-react.md) | Upload, hỏi đáp trực tiếp qua SSE, biểu đồ, báo cáo, trace |
| [08. Đóng gói: Compose, Runner tách riêng, CI](08-dong-goi-va-ci.md) | Runner giữ socket một mình, volume dùng chung, CI không tốn tiền, phát hành image từ tag |
| [09. Đăng nhập bằng link và dashboard vận hành](09-dang-nhap-va-giam-sat.md) | Magic link, cookie phiên, CSRF, vai trò operator, giám sát hệ thống LLM |
| [10. Thiết kế lại giao diện](10-giao-dien-kieu-vercel.md) | Chat làm trung tâm, tiến trình trực tiếp, panel bằng chứng, Tailwind + shadcn/ui |
| [11. Memory: hội thoại và ghi chú theo dataset](11-memory-hoi-thoai-va-dataset.md) | Conversation, ghi chú do người duyệt, snapshot theo run, chống memory poisoning |
| [12. Xuất kết quả](12-xuat-ket-qua.md) | Giới hạn hiển thị và tải về, export bằng job trong sandbox, CSV injection |
| [13. Sửa SQL rồi chạy lại](13-sua-sql.md) | SQL do người gõ, kiểm tra nhiều lớp, lỗi dễ hiểu, không tốn tiền model |
| [14. Tổng quan dữ liệu](14-tong-quan-du-lieu.md) | Profile từng cột lúc build, tách khỏi prompt của agent, đọc số liệu chất lượng dữ liệu |
| [15. Giải thích cách tính](15-giai-thich-cach-tinh.md) | Đọc SQL bằng sqlglot thành lời, thành thật về phần chưa diễn giải, đo độ phủ trước khi mở rộng |
| [16. Định nghĩa chỉ số](16-dinh-nghia-chi-so.md) | Semantic layer nhỏ: lưu từ SQL của câu trả lời, so khớp bằng code, bỏ qua định nghĩa cũ |
| [17. Run manifest](17-run-manifest.md) | Harness: phiên bản agent theo từng attempt, fingerprint của prompt, đánh dấu chạy tiếp qua phiên bản |
| [18. Record/replay](18-record-replay.md) | Harness: ghi output của model rồi phát lại để đo code sau model với $0, không nhiễu |
| [19. pass^k](19-pass-k.md) | Harness: chạy mỗi câu k lần, đo độ ổn định và sàn nhiễu của điểm |
| [20. Circuit breaker và rate limit](20-circuit-breaker-rate-limit.md) | Harness: ngắt route đang lỗi, token bucket trong bộ nhớ và qua Postgres, fail open |
| [21. Hủy, thời hạn và time travel](21-cancel-deadline-time-travel.md) | Harness: dừng hợp tác, ba nguồn dừng, nút Stop, fork từ checkpoint |
| [22. Phản hồi, golden case, đánh giá trực tuyến](22-feedback-goldens.md) | Harness: 👍/👎 kèm lý do, golden case chạy lại khi đổi code, tỷ lệ 👍 theo phiên bản |
| [23. SLO và cảnh báo](23-slo-alerts.md) | Harness: SLI, SLO, error budget, burn rate hai cửa sổ, Prometheus và Alertmanager |
| [24. Thêm file vào dataset](24-dataset-versions.md) | Phiên bản dữ liệu, code quyết định và model chỉ đề xuất, hỏi khi trùng dòng |
| [25. Giao diện tiếng Việt](25-vietnamese-ui.md) | i18n bằng từ điển có kiểu, trật tự từ, định dạng số, mã lỗi API, test chữ cứng |
| [26. Baseline one-shot và lệnh gọi tool bị hỏng](26-baseline-garbled-tool-calls.md) | Baseline công bằng, phủ rộng hay chính xác, chẩn đoán bằng dữ liệu, định dạng harmony, sửa tên tool có giới hạn |
| [27. Xoá dataset](27-xoa-dataset.md) | Xoá dữ liệu người dùng: ẩn trước rồi xoá bằng job nền, kiểm kê chỗ chứa dữ liệu, bước idempotent, chờ mà không tốn lượt thử |
| [28. Phân tích cố định](28-phan-tich-co-dinh.md) | Model chọn loại phân tích, code tính: trusted functions, phân rã cộng tính, "ở đâu" không phải "vì sao", đo bằng dữ liệu cài sẵn đáp án |
