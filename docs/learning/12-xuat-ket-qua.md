# 12. Xuất kết quả: tải đủ dữ liệu, hoặc không tải

## Khái niệm

**Giới hạn hiển thị và giới hạn tải về là hai thứ khác nhau.** Mỗi query của agent chỉ lưu tối đa
50 dòng. Như vậy là đủ cho model đọc và cho bảng xem trước, nhưng không đủ để tải về. Một file
CSV chỉ có 50 dòng đầu trông giống như đầy đủ, và người dùng sẽ phân tích tiếp trên dữ liệu thiếu
mà không biết. Vì vậy quy tắc là: **tải thì phải đủ dữ liệu, nếu không thì không có nút tải**
([ADR 0032](../adr/0032-result-export.md)).

Các hệ thống BI lớn đều tách hai giới hạn này:

| Hệ thống | Hiển thị | Tải về |
|---|---|---|
| Metabase | 2.000 dòng | Chạy lại query, mặc định tối đa 1 triệu dòng |
| Looker | 5.000 dòng | "All results" nếu stream được, có quyền riêng |
| Superset | Theo biểu đồ | Lấy từ results backend hoặc chạy lại, chạy nền qua Celery |
| BigQuery | — | Job `EXPORT DATA` ghi ra storage rồi mới tải |

**Đường đi của một lần tải CSV:**

1. Người dùng bấm "Download CSV" ở tab Data. Trình duyệt gửi
   `POST /v1/runs/{id}/queries/{n}/export`. Request chỉ chứa **số thứ tự** của query, không chứa SQL.
2. API kiểm tra run thuộc workspace, đã xong, và dataset chưa bị build lại từ sau run đó. Sau đó
   API tạo job `export`.
3. Worker đọc câu SQL đã lưu trong kết quả run, cho qua SQL guard một lần nữa, rồi gửi `ExportRun`
   cho Runner.
4. Sandbox mount dataset chỉ đọc ở `/data` và một thư mục rỗng ở `/out`. DuckDB mở với cấu hình đã
   khoá như mọi query, rồi ghi toàn bộ kết quả ra `/out/result.csv`. Vượt 1 triệu dòng, 200 MB hoặc
   120 giây thì không có file nào, chỉ có thông báo lỗi trong `export_report.json`.
5. Worker chuyển file vào `exports/<job id>.csv`. Trình duyệt hỏi trạng thái mỗi giây, rồi tải file
   từ `GET /v1/exports/{id}/file`. File sống 24 giờ.

Biểu đồ thì khác: `make_chart` từ chối kết quả bị cắt, nên biểu đồ luôn chứa đủ các điểm nó vẽ.
Trình duyệt xuất PNG hoặc SVG trực tiếp từ Vega view, không cần server.

## Vì sao chọn như vậy

- **Job nền thay vì gọi thẳng.** Metabase tải trực tiếp vì server ứng dụng được đọc database.
  Ở đây chỉ worker được gọi Runner, và Runner là thứ duy nhất giữ Docker socket (ADR 0024). Đưa
  việc export vào hàng đợi giữ nguyên ranh giới đó, và dùng lại sẵn rate limit, audit, retry, trace.
- **Chỉ nhận số thứ tự, không nhận SQL.** Nếu endpoint nhận SQL, nó thành một cổng chạy SQL tuỳ ý.
  SQL guard vẫn chặn được ghi và đọc file, nhưng bề mặt tấn công rộng hơn không cần thiết.
- **Kiểm tra dataset thay đổi.** Sửa spec sẽ build lại dataset dưới cùng id (ADR 0019). Nếu không
  kiểm tra, file tải về có thể khác dữ liệu mà agent đã dùng để trả lời.
- **Lỗi đi qua report, không qua stderr.** Stderr của sandbox có thể chứa traceback hoặc dữ liệu,
  nên không bao giờ hiển thị cho người dùng. Sandbox ghi thông báo dễ hiểu vào report.
- **Chặn công thức (CSV injection).** Excel có thể chạy một ô như `=HYPERLINK(...)` thành công thức.
  Ô chữ bắt đầu bằng `=`, `+`, `-`, `@`, tab hoặc xuống dòng được thêm `'` phía trước, theo OWASP.
  Số âm vẫn giữ nguyên, vì chỉ ô kiểu chữ mới bị đổi.
- **BOM UTF-8.** Thiếu BOM thì Excel trên Windows đọc "Hà Nội" thành ký tự lạ.

## Phương án đã loại

- **API gọi thẳng Runner rồi stream file.** Nhanh và đơn giản nhất cho người dùng, nhưng API phải
  giữ token của Runner, và request bị giữ lâu khi file lớn.
- **Ghi đủ kết quả của mọi query ngay lúc agent trả lời.** Tốn công cho những file không ai tải,
  và câu trả lời ra chậm hơn.
- **Tải 50 dòng đã lưu.** Làm nhanh nhất, nhưng file trông đầy đủ mà thực ra thiếu.
- **Dùng `COPY ... TO` của DuckDB.** Cần bật truy cập file trong DuckDB, tức là mở lại đúng thứ mà
  cấu hình khoá đang tắt. Ghi bằng module `csv` của Python giữ engine khoá và kiểm soát được giới hạn.

## Câu hỏi phỏng vấn

1. *Vì sao không cho API chạy query để tải về luôn?* Vì ranh giới bảo mật: chỉ một thành phần
   được khởi động sandbox. Đổi lại người dùng phải đợi vài giây cho container khởi động.
2. *Làm sao bảo đảm file không bao giờ bị cắt ngầm?* Đọc theo lô tới tối đa trần + 1 dòng và đếm
   byte khi ghi. Vượt trần thì xoá file và báo lỗi. Có test cho đúng trần và trần + 1.
3. *CSV injection là gì và vì sao chỉ đổi ô kiểu chữ?* Spreadsheet chạy ô bắt đầu bằng `=` như
   công thức. Ô số không thể chứa công thức, và đổi `-5` thành `'-5` sẽ làm hỏng dữ liệu số.
4. *Nếu dataset bị sửa giữa lúc tạo job và lúc chạy?* Worker kiểm tra lại `updated_at` trước khi
   chạy, nên export bị từ chối với thông báo "ask again".
5. *File export là dữ liệu người dùng. Bạn quản lý vòng đời của nó thế nào?* File chỉ sống 24 giờ:
   mỗi lần export mới sẽ xoá file cũ, và API từ chối file quá hạn kể cả khi chưa bị xoá.
