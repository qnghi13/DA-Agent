# 15. Giải thích "cách tính" bằng lời, đọc từ SQL

## Khái niệm

Người không biết SQL nhìn một con số và không có cách kiểm tra: có tính đơn huỷ không, tính
tháng nào, cộng cột nào. Khối **How it was calculated** dưới câu trả lời liệt kê các điều đó bằng
lời:

| Dòng | Lấy từ phần nào của SQL |
|---|---|
| Data | `FROM`, `JOIN … ON` |
| Calculated | `SUM`, `COUNT`, `AVG`…, kể cả `FILTER (WHERE …)` và `SUM(CASE WHEN …)` |
| Only rows where | `WHERE`; điều kiện ngày gộp thành một khoảng "từ … đến …" |
| Grouped by | `GROUP BY`, kể cả `date_trunc`, `strftime`, `year`, `month` |
| Only groups where | `HAVING` |
| Shown | `ORDER BY` và `LIMIT` ("top 5 by …") |

## Quyết định thiết kế

- **Code đọc cây cú pháp (sqlglot), không nhờ model viết.** Model có thể viết "chỉ tính đơn hoàn
  thành" trong khi SQL không lọc gì, và người dùng không phát hiện được. Code thì chỉ nói điều SQL
  thật sự làm, không tốn tiền, không đổi prompt nên không cần eval
  ([ADR 0035](../adr/0035-plain-explanation-from-sql.md)).
- **Thành thật về giới hạn.** Truy vấn con, `WITH`, window function, `UNION` hay hàm lạ được ghi
  vào `unexplained`; giao diện nói "một số bước chưa diễn giải được" và chỉ sang tab SQL. Không bao
  giờ trình bày một phần như thể là toàn bộ.
- **API trả dữ kiện, web viết câu.** Dịch giao diện sau này chỉ sửa `web/src/explain.ts`.
- **Tính khi đọc, không lưu.** Phân tích cú pháp không chạy SQL, nên run cũ cũng có giải thích ngay
  và đổi câu chữ không cần backfill.
- **Dùng chung code với `verify`.** Khoảng thời gian hiện ra chính là khoảng mà bước kiểm tra kỳ
  dùng (`date_bound`), và chỉ giải thích các truy vấn chứa con số của câu trả lời
  (`evidence_indices`).

## Những điều rút ra

- **Đo trước khi tối ưu.** Trên 474 câu SQL agent đã viết (báo cáo eval cục bộ, $0), bản đầu diễn
  giải đủ 81%. Liệt kê phần bị bỏ sót cho thấy vài dạng hay gặp: `STRIP_ACCENTS(LOWER(x)) LIKE`,
  `OR` giữa các `LIKE`, so sánh hai cột, cột yes/no làm điều kiện. Thêm chúng lên 86%. Phần còn lại
  chủ yếu là truy vấn con và `WITH`.
- **Dấu ngoặc đổi nghĩa.** "total of Số lượng × Đơn giá" có thể hiểu là (tổng số lượng) × đơn
  giá; phải viết "total of (Số lượng × Đơn giá)".
- **Lỗi trình bày không được làm hỏng trang.** Nếu bộ đọc gặp lỗi lạ, truy vấn đó được đánh dấu
  chưa diễn giải thay vì làm API trả 500.
