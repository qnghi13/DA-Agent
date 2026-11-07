# 02. Dữ liệu, sandbox và đo lường

## Khái niệm

**Ingestion bằng spec khai báo.** File tải lên (CSV, Excel, SQLite) được đọc thành bảng thô trong
sandbox. Mô hình xem profile của bảng thô rồi đề xuất một `DatasetSpec`: kiểu cột, cách parse
ngày và số, quy tắc loại dòng, bỏ trùng. Code biên dịch spec thành SQL có trích dẫn đúng cách, nên
một spec bị thao túng cũng không chạy được code. Mô hình chỉ đề xuất, còn code kiểm tra: tỉ lệ
parse, dòng tổng phụ còn sót, quy tắc lỡ xoá bản ghi thật. Những vấn đề này được gửi lại để mô
hình sửa, tối đa 3 lần.

**Phòng thủ nhiều lớp cho SQL của agent.** Mỗi truy vấn đi qua ba lớp, và lớp nào cũng phải tự
chặn được tấn công khi các lớp trước đã bị vượt qua:
1. Guard bằng sqlglot: chỉ cho `SELECT` trên bảng của dataset, cấm hàm đọc file.
2. DuckDB mở ở chế độ chỉ đọc, tắt truy cập bên ngoài, khoá cấu hình.
3. Một container mới cho mỗi truy vấn, không có mạng, chỉ mount đúng một dataset.

**Đo đầu-cuối và chẩn đoán theo nhóm lỗi.** Điểm số cho biết agent đúng bao nhiêu, nhưng không cho
biết tại sao sai. Cách làm ở giai đoạn này: chạy baseline, đặt từng câu sai cạnh đáp án, xếp lỗi
vào các **nhóm chung**, sửa nhóm lớn nhất, rồi đo lại. Không sửa riêng cho từng câu, vì như vậy là
khớp vào bộ dev và sẽ không còn đúng trên holdout hay trên file thật.

## Vì sao chọn như vậy

| Quyết định | Lý do chính | ADR |
|---|---|---|
| Spec khai báo, không cho mô hình viết code | Kiểm tra được, chạy lại được, không có code lạ trong pipeline | [0006](../adr/0006-dataset-spec.md) |
| Parse file trong sandbox | File tải lên là dữ liệu không tin cậy; parser có thể có lỗ hổng | [0009](../adr/0009-parse-uploads-in-sandbox.md) |
| Quy tắc khai báo để loại dòng tổng phụ | Hiểu được mọi bố cục; tìm dòng nghi vấn bằng hình dạng dòng thay vì từ khoá | [0010](../adr/0010-exclude-non-record-rows.md) |
| Mỗi truy vấn một container | Cách ly mạnh nhất; khoảng 0,7 giây mỗi truy vấn là chấp nhận được | [0011](../adr/0011-sql-per-request-sandbox.md) |
| Giữ tên cột gốc, liệt kê giá trị, bỏ dòng trùng có khoá | Ba nhóm lỗi lớn nhất khi chẩn đoán | [0012](../adr/0012-schema-names-values-and-double-entries.md) |

**Bài học từ các baseline** (bộ `bootstrap`, 30 câu, đúng / sai / bỏ trống):

| Lượt | Thay đổi | Kết quả | Điểm |
|---|---|---|---|
| A | `pos.sqlite` gốc | 3 / 8 / 19 | −5 |
| B2 | Dataset sạch qua sandbox, liệt kê giá trị | 6 / 16 / 8 | −10 |
| B3 | Giữ tên cột gốc | 6 / 15 / 9 | −9 |
| B4 | Bỏ dòng trùng khi bảng có khoá | 16 / 3 / 11 | +13 |
| B5 | Cho biết mỗi dòng là gì, hướng dẫn đọc giá trị trạng thái | 18 / 4 / 8 | +14 |

- **Lỗi lớn nhất nằm ở dữ liệu, không nằm ở mô hình.** 1.208 dòng nhập hai lần làm mọi tổng lệch
  khoảng 2%. Agent viết SQL đúng định nghĩa nhưng vẫn sai, và chỉ đối chiếu với đáp án mới thấy.
- **Trả lời nhiều hơn chưa chắc tốt hơn.** Khi chấm +1 / −1 / 0, một câu trả lời sai còn tệ hơn
  bỏ trống. Lượt B2 trả lời gấp đôi lượt A nhưng điểm lại thấp hơn.
- **Dữ liệu đúng chưa chắc tăng điểm.** Loại dòng tổng phụ đạt precision và recall 100%, nhưng so
  có và không trên 5 shop thì điểm như nhau (+4 so với +5). Lý do: ở 4/5 shop dòng tổng phụ không có
  ngày, và câu hỏi nào cũng lọc theo ngày. Trên chính 5 shop đó, việc cho agent biết "mỗi dòng là
  một dòng hàng" và cách đọc giá trị trạng thái lại nâng điểm từ +4 lên +10.
- **Bỏ trùng phải có bằng chứng.** Hai dòng giống hệt nhau có cùng mã đơn và số dòng thì chắc chắn
  là nhập hai lần. Sổ bán hàng không có mã thì có thể là hai lần bán thật. Đáp án biz_107 xác nhận
  điều này: giữ nguyên các dòng trùng mới ra đúng.

## Phương án đã loại

- **Để mô hình viết code Python làm sạch:** linh hoạt hơn nhưng khó kiểm tra và phải tin code lạ.
  Có thể thêm sau như hướng lai, chạy trong sandbox.
- **Lọc dòng tổng phụ bằng danh sách từ khoá:** phụ thuộc ngôn ngữ, và lỡ xoá sản phẩm tên "Total".
- **Bỏ mọi dòng trùng hoàn toàn:** sai với sổ bán hàng không có mã.
- **Một container dùng lâu cho nhiều truy vấn:** nhanh hơn, nhưng các truy vấn có thể ảnh hưởng nhau.
- **Đổi tên cột trong tài liệu cho khớp snake_case:** tài liệu là văn bản tự do do người dùng viết.

## Câu hỏi phỏng vấn

**Làm sao biết một thay đổi thật sự giúp được?**
Chạy cùng bộ câu hỏi trước và sau khi thay đổi, giữ nguyên mọi thứ khác (dùng nhóm đối chứng như
`datasets-dev-noexclude`), rồi so số câu đúng và sai. Với 30 câu và một mô hình dao động giữa các
lần chạy, chênh lệch dưới khoảng 3 câu là nhiễu. Nên xem từng câu bị đổi kết quả để biết thay đổi có
đúng là nguyên nhân không.

**Đọc đáp án tập dev có phải là gian lận không?**
Không, nếu chỉ dùng để tìm nhóm lỗi chung và holdout vẫn đóng kín tới cuối. Rủi ro là vô tình sửa
cho khớp từng câu. Cách kiểm soát: mỗi bản sửa phải đúng cho mọi file tải lên (ví dụ "bỏ trùng khi
có khoá"), không chứa giá trị cụ thể của shop nào, và agent cùng sandbox không bao giờ thấy đáp án.

**Vì sao ba lớp bảo vệ SQL mà không một lớp?**
Guard bằng parser có thể bỏ sót một hàm mới của DuckDB. Engine có thể có lỗi cấu hình. Container
giới hạn thiệt hại nếu cả hai lớp trên hỏng. Mỗi lớp đều có test tấn công riêng, giả định các lớp
khác đã bị vượt qua.

**Khi nào để code quyết định thay vì mô hình?**
Khi code chứng minh được. Có khoá thì dòng trùng chắc chắn là lỗi, nên code tự bỏ. Còn một dòng
thưa có phải dòng tổng phụ không thì cần xét giá trị, nên để mô hình quyết, code chỉ kiểm tra và
gửi phản hồi lại.
