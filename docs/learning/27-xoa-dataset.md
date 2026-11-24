# 27. Xoá dataset

Xoá dữ liệu của người dùng nghe đơn giản, nhưng khó ở chỗ **dữ liệu nằm ở nhiều nơi hơn ta nghĩ**.
Bài học chung: **kiểm kê trước khi viết code xoá, và thiết kế để bước nào chạy lại cũng an toàn.**
(ADR 0050.)

## Kiểm kê: dữ liệu nằm ở đâu?

ADR 0004 từng viết "xoá dataset là xoá một file". Sau nhiều tính năng, một dataset để lại dấu ở:

- Postgres: dataset, các phiên bản, ghi chú, metric, hội thoại, mọi job (câu hỏi, câu trả lời,
  SQL, kết quả), sự kiện tiến trình, phản hồi, golden case.
- Ổ đĩa: file upload của mọi phiên bản, bản build DuckDB, file CSV xuất ra.
- Checkpoint của LangGraph: mỗi run một thread, cộng các thread fork khi du hành thời gian.
- Ngoài tầm với: trace Phoenix, log container, file báo cáo golden do operator chạy.

Cái gì không nằm trong bảng kiểm kê thì sẽ bị bỏ sót. Phần "ngoài tầm với" được ghi rõ vào threat
model thay vì giả vờ đã xoá.

## Ẩn ngay, xoá sau

API chỉ làm việc nhanh và chắc: trong một transaction đánh dấu `deleting`, huỷ các job đang mở,
xếp một job `delete`. Từ lúc đó mọi route trả 404 cho dataset và job của nó. Việc nặng (xoá file,
checkpoint, dòng) để worker làm. Hai lựa chọn đã bỏ:

- Xoá ngay trong API, trả 409 khi còn job chạy: người dùng phải đợi rồi hỏi lại, và nếu sập giữa
  chừng thì file mồ côi không còn dòng nào trỏ tới.
- Soft delete (chỉ ẩn): dữ liệu vẫn còn nguyên, mất mục đích xoá.

## Chặn race

Một request đọc dataset ngay trước khi lệnh xoá commit vẫn có thể xếp job mới. Cách chặn: mọi
endpoint xếp job khoá dòng dataset bằng `FOR SHARE` trong cùng transaction, còn lệnh xoá dùng
`FOR UPDATE`. Hai bên phải xếp hàng, nên job xếp trước sẽ bị lệnh xoá nhìn thấy và huỷ. Phía job
kết thúc muộn cũng có chốt: các lệnh ghi trạng thái có `status <> 'deleting'`, nên một ingest xong
trong lúc xoá không "hồi sinh" dataset.

## Idempotent và thứ tự

Job xoá làm theo thứ tự: dừng job khác, xoá file, xoá checkpoint, **cuối cùng mới xoá dòng**. Dòng
trong database là bản ghi cho biết còn gì phải xoá, nên nếu worker sập giữa chừng, lần thử lại vẫn
biết phải làm gì. Mỗi bước chịu được việc chạy lại (file thiếu không phải lỗi). Đường dẫn chỉ dựng
từ id trong database và phải là con trực tiếp của thư mục gốc, để một giá trị lạ không xoá nhầm
chỗ khác.

## Chờ mà không tốn lượt thử

Không được xoá file dưới chân một job đang chạy. Job xoá thấy job khác còn chạy thì không thất bại:
nó trả lại hàng đợi với `run_after` muộn hơn và **hoàn lại lượt thử** (kết quả attempt `wait`).
Nếu coi chờ là thất bại, job sẽ hết lượt thử chỉ vì một câu hỏi chạy lâu. Có giới hạn an toàn: chờ
quá 2 giờ thì job thất bại và hiện ở ops, dữ liệu vẫn ẩn, người dùng bấm xoá lại để xếp job mới.

## Cái giữ lại có chủ đích

`llm_calls` (token và tiền, không có giá trị dữ liệu) và `audit_log` được giữ, vì ngân sách ngày và
báo cáo chi phí phải đúng. Hệ quả chấp nhận: số liệu ops tính từ bảng `jobs` mất các job đã xoá.

## Lỗi cũ tìm ra trên đường

Khi kiểm kê thấy ingest và build không bao giờ xoá thư mục staging, và không gì liên kết thư mục đó
với dataset. Sửa: handler tự dọn khi xong, và worker dọn staging cũ hơn 24 giờ khi khởi động và
trong mỗi job xoá. Kiểm kê để xoá cũng là cách tìm rò rỉ.
