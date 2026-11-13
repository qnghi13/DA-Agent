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
