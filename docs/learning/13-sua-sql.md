# 13. Sửa SQL rồi chạy lại

## Khái niệm

**DA tin một con số khi tự kiểm tra được nó.** Cách kiểm tra quen thuộc nhất là sửa câu SQL phía
sau con số đó: bỏ một điều kiện lọc, thêm một cột, đổi cách group. Nếu phải hỏi lại model thì vừa
tốn tiền vừa có thể đổi nhiều thứ hơn mình muốn. Các sản phẩm như Databricks Genie, Snowflake
Cortex Analyst, Metabase đều hiện SQL do máy viết và cho người biết SQL sửa nó. Người không biết
SQL vẫn hỏi tiếp bằng câu chữ trong khung chat.

**SQL do người gõ không nguy hiểm hơn SQL do model viết.** Model có thể bị prompt injection, nên
từ đầu dự án đã coi SQL của model là không đáng tin. SQL của người dùng đi đúng con đường đó
([ADR 0033](../adr/0033-edited-sql-as-a-query-job.md)).

## Ba lớp kiểm tra

| Lớp | Chặn gì | Khi nào |
|---|---|---|
| SQL guard ở API (`validate_select`) | Gõ sai cú pháp, gõ câu văn, `DROP`/`DELETE`/`COPY`, hai lệnh nối nhau, đọc file | Trước khi tạo job: trả 422 ngay, không tốn sandbox |
| SQL guard ở worker | Như trên, phòng khi payload trong database bị sửa | Trước khi gọi Runner |
| Sandbox + DuckDB khoá | Mọi thứ còn lọt: không mạng, không khoá API, chỉ một dataset chỉ đọc, tắt truy cập file ngoài | Khi chạy |

Sai tên cột thì guard không biết (nó không đọc schema), nên DuckDB trong sandbox báo lỗi. Thông báo
của DuckDB rất có ích: `Referenced column "totl" not found. Candidate bindings: "total"`.

## Lỗi phải dễ đọc

- Thông báo của sqlglot có mã màu terminal (`\x1b[4m` để gạch chân). Trên web chúng thành ký tự
  rác, và model cũng phải đọc chúng. Guard giờ bỏ các mã này.
- Lỗi DuckDB đi qua `SqlResult.error`, không bao giờ qua stderr của sandbox.
- SQL gốc của agent và câu trả lời không bị đổi. Nút **Reset** quay về SQL gốc.

## Những điều rút ra

- **Kiểm tra sớm ở API, kiểm tra lại ở worker.** Kiểm tra ở API cho phản hồi tức thì; kiểm tra ở
  worker là vì payload nằm trong database, và dữ liệu trong database không được tin.
- **Không tốn tiền model thì không chặn theo ngân sách model.** Query và export chỉ bị giới hạn
  theo số job mỗi phút.
- **Audit ghi độ dài, không ghi nội dung SQL**, vì SQL có thể chứa giá trị lấy từ dữ liệu.
- **Thử cả đường xấu trên trình duyệt thật:** gõ sai, gõ câu tiếng Việt, `DROP`, hai lệnh, sai tên
  cột. Mỗi trường hợp phải hiện một câu người đọc hiểu được ngay dưới ô soạn.
