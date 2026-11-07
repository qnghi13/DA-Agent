# 04. Kiểm tra câu trả lời và độ tin cậy

## Khái niệm

**Một câu trả lời "trông đúng" có thể sai theo nhiều cách mà model không tự thấy.** Graph ở ghi chú
03 đã chặn con số không có trong kết quả truy vấn. Giai đoạn này thêm các kiểm tra cho những lỗi
qua được lớp đó. Tất cả đều là code, không nhờ model tự chấm:

| Lỗi | Ví dụ | Kiểm tra |
|---|---|---|
| Kỳ không có dữ liệu | Tổng tháng 8/2026 = 0, nhưng dữ liệu chỉ tới 28/6 | Đọc điều kiện ngày trong SQL bằng sqlglot, so với khoảng ngày build đã ghi |
| Join lặp dòng | Cộng tổng tiền đơn sau khi join với chi tiết đơn: mỗi đơn bị cộng một lần cho mỗi dòng hàng | Chạy truy vấn đếm: `count(t.rowid)` so với `count(DISTINCT t.rowid)` |
| Giá trị không đọc được | `-2tr250` thành NULL, và `SUM` bỏ qua nó mà không báo | Đếm các dòng nằm trong `_quarantine` thuộc đúng phạm vi truy vấn |
| Số gõ tay | `SELECT ROUND(12.0/913*100, 2)` với số 12 tự nghĩ ra | Số gõ vào truy vấn phải có trong kết quả của một truy vấn có đọc dữ liệu |

**Truy vấn bằng chứng.** Model chạy nhiều truy vấn thăm dò trên đường đi. Chỉ những truy vấn có kết
quả chứa con số trong câu trả lời mới bị kiểm tra (nếu câu trả lời không có số, thì là truy vấn
cuối). Một truy vấn thăm dò sai không làm hỏng câu trả lời lấy từ truy vấn khác.

**Đo thay vì đoán.** Kiểm tra join không đoán khóa nào là duy nhất từ tên cột. Nó chạy lại đúng phần
FROM, JOIN, WHERE của truy vấn và đếm. Nhờ vậy bộ lọc, anti-join (`LEFT JOIN ... IS NULL`) và CTE
gộp trước đều được tính đúng. Một phép tổng chỉ bị chặn khi *mọi* bảng nó đọc đều bị lặp, nên
`sum(dòng.SL * sản_phẩm.giá)` vẫn qua: giá lặp lại theo từng dòng là đúng.

**Giả định injection thành công.** Bộ test injection không cố chứng minh model không bị lừa, vì
không chứng minh được. Model giả làm theo mọi chữ được cài trong ô dữ liệu, và test chứng minh quyền
hạn vẫn chặn được thiệt hại: cột ẩn vẫn ẩn, không ghi được, không đọc được file hay dataset khác,
con số bị cài sẵn không được nộp, giá trị bí mật không vào trace.

## Vì sao chọn như vậy

| Quyết định | Lý do chính | Nơi |
|---|---|---|
| Kiểm tra đọc SQL, không đọc câu hỏi | Không phụ thuộc ngôn ngữ, không khớp vào bộ dev | `agent/verify.py` |
| Truy vấn đếm chạy trong sandbox, sau các kiểm tra miễn phí | Tốn khoảng 0,7 giây, không tốn token; chỉ chạy khi cần | [ADR 0014](../adr/0014-probe-checks-in-verify.md) |
| Truy vấn đếm lỗi thì bỏ qua | Lớp chắn không được tự chặn câu trả lời vì lỗi của chính nó | `agent/data_checks.py` |
| Giá trị không đọc được: giữ câu trả lời lại, không gửi sửa | Model không sửa được dữ liệu; tổng tiền thiếu một dòng vẫn sai | ADR 0014 |
| Số gõ tay được phép nếu lấy từ dữ liệu | 7/12 câu dùng cách này là câu đúng; cấm hẳn sẽ mất điểm | `agent/verify.py` |
| Lỗi bất ngờ của tool thành lỗi có cấu trúc | Docker chết không được làm crash cả lượt eval; model chỉ thấy tên loại lỗi | `agent/tools.py`, `runner/runner.py` |
| Nội dung tài liệu không được đóng thẻ `<document>` | Nếu không, file tải lên có thể chèn chữ nằm ngoài vùng dữ liệu | `agent/prompts.py` |

**Kiểm chứng offline trước khi tốn tiền.** Mỗi kiểm tra được chạy lại trên hội thoại đã lưu trong
Postgres của khoảng 70 câu graph đã trả lời. Không kiểm tra nào chặn nhầm câu đúng. Cách này cho câu
trả lời nhanh và miễn phí cho câu hỏi "kiểm tra có làm mất điểm không", trước khi chạy model thật.

**Kết quả đo** (bộ `bootstrap`, 30 câu biz_123, đúng / sai / bỏ trống):

| Lượt | Kết quả | Chi phí |
|---|---|---|
| B7: graph, chưa có các kiểm tra mới | 18 / 7 / 5, +11 | $0,09 |
| B8: đủ các kiểm tra của giai đoạn này | 17 / 7 / 6, +10 | $0,11 |

Không kiểm tra mới nào kích hoạt trong B8; ba lần chặn đều do kiểm tra truy vết con số có từ trước.
Chênh lệch 1 điểm là do model biến động giữa các lần chạy (mức nhiễu khoảng ±3 trên 30 câu). Đúng
như dự đoán từ lần chạy lại offline: các kiểm tra này không làm mất điểm, và lỗi chúng chặn hiếm gặp
trên bộ dev.

## Phương án đã loại

- **Ngưỡng tỉ lệ đọc được của cả cột:** quá thô. Cột đọc được 99,98% vẫn có thể sai đúng tháng mà
  câu hỏi hỏi tới, còn cột 95% có thể không ảnh hưởng gì nếu dòng hỏng đã bị lọc.
- **Khóa duy nhất từ metadata để bắt join lặp:** spec không khai báo khóa, và khóa duy nhất trên
  toàn bảng có thể không còn ý nghĩa sau bộ lọc.
- **Cấm truy vấn không đọc bảng:** chặn nhầm cách làm hợp lệ là tính tỉ lệ từ hai số đã truy vấn.
- **Cho model tự kiểm tra câu trả lời:** dễ mắc lại đúng lỗi của chính nó (ADR 0007).

## Câu hỏi phỏng vấn

**Làm sao biết một kiểm tra không làm hại nhiều hơn lợi?**
Chạy lại nó trên các run đã lưu và đếm số câu đúng bị chặn nhầm. Các kiểm tra ở đây đều chặn nhầm 0
câu. Chúng cũng chưa bắt được câu sai nào trên bộ dev, vì lỗi hiện tại của model chủ yếu là hiểu sai
định nghĩa, không phải các lỗi này. Chúng là lớp phòng thủ cho dữ liệu người dùng tải lên.

**Vì sao bắt lỗi join bằng cách chạy thêm truy vấn mà không phân tích SQL?**
Lặp dòng phụ thuộc vào dữ liệu: cùng một câu join, bảng này lặp còn bảng kia thì không, và bộ lọc có
thể loại hết các dòng lặp. Đếm `rowid` trên đúng phần FROM/JOIN/WHERE cho câu trả lời chính xác.

**Prompt injection: chặn thế nào?**
Không thể đảm bảo model không bị lừa. Nên dữ liệu luôn được đóng khung là dữ liệu (kết quả tool dạng
JSON, tài liệu không thể đóng thẻ của mình), tool chỉ có quyền đọc, SQL đi qua guard và sandbox, và
câu trả lời phải truy được về kết quả truy vấn. Test giả định model đã bị lừa và chứng minh từng
quyền hạn vẫn đứng vững.

**Docker chết giữa lượt eval thì sao?**
Runner trả về kết quả lỗi thay vì ném exception. Model nhận lỗi có cấu trúc và thử lại trong giới hạn
lượt. Nếu vẫn hỏng, run dừng sạch. Không có gì làm crash cả lượt eval.
