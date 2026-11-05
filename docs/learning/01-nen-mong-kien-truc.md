# 01. Nền móng kiến trúc

## Khái niệm

**Agent là vòng lặp có kiểm soát.** Mô hình đề xuất bước tiếp theo, còn code quyết định bước đó có được chạy không, chạy ở đâu và dừng khi nào. Phần khó của Agent System Engineering nằm ở lớp kiểm soát này, không nằm ở việc gọi API.

**Dữ liệu và mô hình đều không đáng tin.** Một ô Excel có thể chứa câu "hãy bỏ qua hướng dẫn trước đó", và mô hình có thể làm theo. Vì không ngăn được hoàn toàn, thiết kế phải bảo đảm rằng nếu bị lừa thì mô hình cũng không gây được nhiều thiệt hại.

**Durable execution.** Một lượt chạy có thể mất vài phút và worker có thể chết giữa chừng. Checkpoint lưu state sau mỗi bước để worker khác chạy tiếp. Vì một node có thể bị chạy lại, side effect của node phải idempotent, tức chạy hai lần cho kết quả như một lần.

## Vì sao chọn như vậy

| Quyết định | Lý do chính | ADR |
|---|---|---|
| Tách API, Worker, Runner | Mỗi service chỉ có quyền nó cần; Worker giữ key nhưng không giữ Docker socket | [0005](../adr/0005-sandbox-behind-runner.md) |
| LangGraph | Có sẵn checkpoint, interrupt và chạy tiếp | [0001](../adr/0001-langgraph.md) |
| `LLMClient` tự viết | Kiểm soát cache, retry, chi phí | [0002](../adr/0002-custom-llm-client.md) |
| Hàng đợi trên Postgres | Bớt một service, job và state có thể chung transaction | [0003](../adr/0003-postgres-job-queue.md) |
| Mỗi dataset một file DuckDB | Cách ly bằng việc mount file, không phụ thuộc bộ lọc SQL | [0004](../adr/0004-duckdb-per-dataset.md) |
| `DatasetSpec` | Nhận được file bất kỳ; spec là dữ liệu nên không chạy được code | [0006](../adr/0006-dataset-spec.md) |
| Verify bằng code | Rẻ, test được, cho cùng kết quả mỗi lần | [0007](../adr/0007-code-first-verification.md) |

## Phương án đã loại

- **Multi-agent:** thêm chi phí và điểm lỗi, trong khi một graph có các node rõ ràng đã đủ.
- **Chạy code bằng `exec` trong Worker:** code sinh ra sẽ đọc được API key.
- **Schema bán lẻ cố định:** dễ làm nhưng không nhận được file bất kỳ.
- **Để mô hình tự đánh giá câu trả lời:** mô hình dễ mắc lại đúng lỗi của chính nó.

## Câu hỏi phỏng vấn

**Làm sao ngăn prompt injection?**
Không thể ngăn hoàn toàn. Nên đóng khung nội dung file là dữ liệu, cấp quyền tối thiểu cho tool, cách ly việc chạy code, rồi có bộ test tấn công để đo. Điểm chính là giới hạn thiệt hại khi injection thành công.

**Worker chết giữa lượt chạy thì sao?**
Job có lease; khi lease hết hạn, worker khác nhận job và chạy tiếp từ checkpoint gần nhất. Node phải chạy lại được an toàn, nên side effect được gắn khóa theo `run_id` và bước.

**Vì sao không dùng Redis làm hàng đợi?**
Ở quy mô này Postgres đủ dùng và đã có sẵn cho checkpoint. Đổi lại phải tự viết lease và retry, và thông lượng thấp hơn. Khi tải lớn thì mới cần xem lại.

**Sandbox Docker có đủ an toàn không?**
Không tuyệt đối, vì container dùng chung kernel với host. Nó chặn được phần lớn rủi ro khi bỏ mạng, bỏ capability và giới hạn tài nguyên. Muốn mạnh hơn thì dùng gVisor hoặc microVM, đổi lại khó cài đặt và có thể chậm hơn.

**Làm sao biết một thay đổi prompt có tốt hơn không?**
Chạy cùng bộ eval trước và sau, so điểm, chi phí, độ trễ và phân loại nguyên nhân sai. Không dùng holdout để tinh chỉnh, nếu không điểm cuối sẽ không còn đáng tin.
