# 16. Định nghĩa chỉ số: lưu từ một câu trả lời đúng

## Khái niệm

Người không chuyên hỏi bằng từ kinh doanh: "doanh thu", "đơn thành công", "khách quay lại". Mỗi từ
có nhiều cách tính (có trừ đơn huỷ không, tính theo dòng hàng hay theo đơn), và agent phải đoán.
Một **định nghĩa chỉ số** (metric definition) gắn một từ với đúng một cách tính đã được người dùng
xác nhận:

| Phần | Ví dụ | Có trong định nghĩa? |
|---|---|---|
| Bảng | `orders` | Có |
| Phép tính | `SUM("so_luong" * "don_gia")` | Có |
| Bộ lọc dòng | `"trang_thai" = 'completed'` | Có |
| Kỳ, nhóm, sắp xếp, giới hạn | tháng 6, theo tháng, top 5 | Không: "doanh thu" tháng 5 và tháng 6 tính như nhau |

Đây là ý tưởng của **semantic layer** (dbt MetricFlow, Looker, Cube) và của Databricks Genie, Snowflake
Cortex Analyst: định nghĩa một lần, mọi câu hỏi dùng chung.

## Quyết định thiết kế

- **Lưu từ câu trả lời, không bắt người dùng viết.** Dưới "How it was calculated" có nút "Save as a
  definition". Server đọc lại SQL đã lưu của run bằng sqlglot; body chỉ gửi run, câu truy vấn, phép
  tính và tên, không bao giờ gửi SQL. Nhờ vậy không lưu được câu SQL chưa từng chạy
  ([ADR 0036](../adr/0036-metric-definitions.md)).
- **Là một loại memory.** Dùng lại `dataset_memories` của [ADR 0030](../adr/0030-conversation-and-dataset-memory.md):
  chỉ người ghi, có audit, snapshot theo run, chuỗi `superseded_by`. Không cần bảng hay dịch vụ mới.
- **Một tên, một định nghĩa.** Lưu trùng tên hoặc tên gọi khác (so không dấu, không phân biệt hoa
  thường) thì thay định nghĩa cũ. Khoá advisory theo dataset để hai lần lưu đồng thời không cùng tồn tại.
- **Dạng chuẩn để so khớp.** SQL được đưa về một dạng: bỏ alias và tên bảng đứng trước cột, chuẩn
  hoá tên, `!=` thành `<>`, `COUNT(1)` thành `COUNT(*)`, bỏ `ROUND`. Điều kiện trong `FILTER (WHERE …)`
  hay `SUM(CASE WHEN … THEN x ELSE 0 END)` được tách thành bộ lọc, nên hai cách viết của một chỉ số là
  một.
- **Huy hiệu do code quyết định.** "Used your definition of Revenue" chỉ hiện khi SQL của câu trả lời
  có đúng phép tính và đủ các bộ lọc của định nghĩa. Model không tự nhận. Chỉ xét định nghĩa có trong
  snapshot của run.
- **Định nghĩa cũ bị bỏ qua.** Dataset build lại mà mất cột thì định nghĩa không vào prompt, và panel
  báo "stale".

## Phương án đã loại

- **Verify ép dùng định nghĩa:** phải nhận ra tên chỉ số trong câu tiếng Việt tự do ("doanh số",
  "DT", "tiền bán"), dễ bắt sửa nhầm và tốn vòng gọi model. Để sau, khi eval cho thấy cần.
- **Tool `compute_metric` để code tự viết SQL:** chắc chắn nhất nhưng lớn, kém linh hoạt.
- **Agent hỏi lại khi gặp từ chưa định nghĩa:** giai đoạn 2; cần nối phần trả lời clarify qua API và
  web (graph đã hỗ trợ `Command(resume=...)`).

## Những điều rút ra

- **Không đổi prompt khi không có định nghĩa.** Một test so từng byte output của tài liệu ghi chú với
  bản chụp trước khi sửa. Eval chuẩn không dùng memory, nên không cần chạy lại `regression`.
- **Kiểm tra thật trên trình duyệt:** lưu "Revenue" từ câu hỏi tháng 6 tiếng Anh, rồi hỏi "doanh thu
  tháng 5 năm 2026" bằng tiếng Việt: agent dùng đúng định nghĩa và huy hiệu hiện ra ($0.0005).

## Câu hỏi phỏng vấn có thể gặp

1. Semantic layer là gì, và vì sao agent text-to-SQL cần nó?
2. Vì sao định nghĩa được dựng lại ở server thay vì nhận từ client?
3. Làm sao biết một câu trả lời đã dùng định nghĩa mà không hỏi model?
4. Khi dữ liệu đổi schema, memory cũ có thể gây hại thế nào, và bạn chặn ra sao?
5. Khi nào nên chuyển từ "ghi chú trong prompt" sang "tool sinh SQL"?
