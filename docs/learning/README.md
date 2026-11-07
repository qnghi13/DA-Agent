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
