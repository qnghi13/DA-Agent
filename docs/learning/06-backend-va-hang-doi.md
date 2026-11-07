# 06. Backend: hàng đợi, worker, API và giới hạn

## Khái niệm

**Request không làm việc nặng.** Một câu hỏi có thể chạy vài phút: nhiều lượt gọi model, mỗi query
một container. Nếu handler HTTP tự làm việc đó, request treo lâu, timeout của proxy cắt ngang, và
server chết là mất luôn công việc. Vì vậy API chỉ kiểm tra quyền, ghi một job vào Postgres rồi trả
`202` ngay. Worker, một process khác, nhận job và chạy graph.

```
client ─HTTP─> API ──ghi job──> Postgres <──claim── worker ──> Runner ──> sandbox
          <─SSE── API <──đọc run_events──┘           └─ghi run_events, llm_calls
```

**Hàng đợi trên Postgres.** Worker claim job bằng một câu `UPDATE ... WHERE id = (SELECT ...
FOR UPDATE SKIP LOCKED LIMIT 1)`. Mỗi job có một *lease*: hạn giữ job, tính bằng đồng hồ của
database. Worker gia hạn bằng *heartbeat* ở một luồng nền. Worker chết thì ngừng gia hạn, lease hết,
và worker khác claim lại job. Mọi lệnh ghi kết quả đều khớp `worker_id`, nên worker cũ không ghi đè
kết quả của worker mới.

**At-least-once và idempotent.** Một job có thể chạy hai lần (worker chậm nhưng chưa chết). Hệ thống
an toàn vì run job dùng id của job làm `thread_id` của LangGraph: lần chạy sau tiếp tục từ checkpoint
và lặp lại nhiều nhất một lượt model; tool chỉ đọc. Ledger chi phí nằm trong Postgres, nên budget
của run không bị đặt lại khi job chuyển worker.

**Cô lập workspace.** Mọi câu SQL đọc dataset, job, sự kiện đều có `workspace_id`. Dữ liệu của
workspace khác trả 404, không phải 403, để id không lộ ra là có tồn tại. Run job chỉ mở được dataset
`ready` của chính workspace đó.

**Tiến trình theo thời gian thực.** Graph chạy bằng `stream` và mỗi cập nhật node thành một sự kiện
nhỏ (plan, tool, verify, done), không chứa dữ liệu dòng. Worker ghi vào bảng `run_events`; API gửi
bằng SSE với id của bảng làm id sự kiện, nên client nối lại bằng `Last-Event-ID` không mất gì.

## Vì sao chọn như vậy

| Quyết định | Lý do chính | Nơi |
|---|---|---|
| Queue trên Postgres, không Redis | Ít service hơn; job và trạng thái dataset đổi trong cùng transaction | ADR 0003, [0017](../adr/0017-queue-leases-and-sql-migrations.md) |
| Lease theo `now()` của database | Lệch giờ giữa các máy không làm hết hạn sớm hay muộn | ADR 0017 |
| Heartbeat ở luồng riêng, connection riêng | Một connection psycopg không được dùng đồng thời từ hai luồng | [ADR 0018](../adr/0018-worker.md) |
| Ba kiểu kết thúc job | Lỗi tạm thì retry có backoff; lỗi vĩnh viễn và lỗi lạ thì dừng, chỉ lưu tên loại lỗi | ADR 0018 |
| Spec sửa và job build ghi chung transaction | Không bao giờ có dataset `building` mà không có job | [ADR 0019](../adr/0019-dataset-lifecycle.md) |
| Key SHA-256, so sánh thời gian hằng số | Key có 256 bit ngẫu nhiên; hash chậm chỉ cần cho mật khẩu dễ đoán | [ADR 0020](../adr/0020-http-api.md) |
| SSE từ bảng, polling 0,5 giây | Nối lại không mất sự kiện; NOTIFY bị mất khi không ai nghe | [ADR 0021](../adr/0021-progress-events.md) |
| Rate limit cửa sổ cố định trong Postgres | Một câu upsert nguyên tử, đúng khi có nhiều process API | [ADR 0022](../adr/0022-rate-and-money-limits.md) |

**Kiểm tra test bằng cách tạm phá code.** Test pass chưa chắc đã bảo vệ điều mình nghĩ. Mỗi tính chất
quan trọng được kiểm bằng cách tạm phá code rồi chạy lại: bỏ `SKIP LOCKED`, tắt heartbeat, dùng
`thread_id` ngẫu nhiên, bỏ bộ lọc workspace. Lần đầu bỏ `SKIP LOCKED` mà test vẫn pass: Postgres kiểm
tra lại dòng sau khi chờ khóa nên vẫn đúng, chỉ chậm. Test mới giữ khóa một dòng ở connection khác
và chứng minh claim bỏ qua nó thay vì chờ.

**Chạy thật từ đầu đến cuối.** File CSV tổng hợp 300 đơn hàng, có dòng tổng ở cuối. Upload qua HTTP,
LLM đề xuất spec (đọc ngày `dmy`, loại dòng `TOTAL`, lấy quy tắc doanh thu từ README), duyệt, hỏi
"Doanh thu tháng 6/2026?", theo dõi SSE, tải báo cáo. Đáp án 33.530.000 khớp với con số tính trước,
số trong báo cáo trỏ đúng ô của tháng 6, biểu đồ vẽ được. Tổng chi phí $0,0014. Lượt chạy cũng lộ ra
hai lỗi nhỏ mà unit test không thấy: năm "2026" trong câu giải thích bị đánh dấu không truy được, và
trục chữ của biểu đồ bị sắp theo bảng chữ cái.

## Phương án đã loại

- **Gọi model ngay trong request:** request treo, timeout cắt ngang, server chết là mất việc.
- **Redis với Celery:** thêm một service và một nơi thứ hai giữ trạng thái job.
- **Lease theo đồng hồ của worker:** lệch giờ giữa các máy.
- **Retry mọi exception:** một bug chạy lại đến hết số lần thử, mỗi lần đều tốn tiền model.
- **WebSocket:** luồng chỉ đi một chiều; SSE có sẵn cơ chế nối lại.
- **Đếm rate limit trong bộ nhớ:** mỗi process API đếm riêng.

## Câu hỏi phỏng vấn

**Vì sao không gọi LLM ngay trong request HTTP?**
Việc chạy lâu và có thể hỏng giữa chừng. Tách thành job giúp trả lời ngay, retry được, sống sót khi
server khởi động lại, và giới hạn được số việc chạy đồng thời bằng số worker.

**`FOR UPDATE SKIP LOCKED` giải quyết điều gì?**
Hai worker không nhận cùng một job, và không phải chờ nhau: dòng đang bị khóa thì bỏ qua sang dòng
khác. Không có `SKIP LOCKED`, Postgres vẫn đúng nhờ kiểm tra lại dòng sau khi chờ, nhưng các worker
xếp hàng sau nhau.

**Worker chết giữa chừng thì sao?**
Lease hết hạn, worker khác claim lại. Run tiếp tục từ checkpoint của LangGraph theo id của job. Lệnh
ghi của worker cũ bị từ chối vì sai `worker_id`. Job làm chết worker liên tục sẽ dừng sau số lần thử
tối đa.

**Exactly-once có làm được không?**
Không, với hệ phân tán thì thực tế là at-least-once cộng xử lý idempotent. Ở đây: checkpoint theo id
job, tool chỉ đọc, sự kiện có thể lặp một bước, ledger chi phí tính tổng theo run.

**Lưu API key thế nào?**
Chỉ lưu hash, hiện key một lần. Key ngẫu nhiên đủ dài thì SHA-256 là đủ; bcrypt dành cho mật khẩu do
người đặt. So sánh bằng `hmac.compare_digest`, kể cả khi không tìm thấy prefix, để thời gian phản hồi
không lộ thông tin.

**Làm sao một client mất mạng không mất tiến trình?**
Sự kiện được lưu trong bảng có id tăng dần. SSE gửi id đó; client nối lại với `Last-Event-ID` và chỉ
nhận phần còn thiếu. Trước khi gửi `end`, server đọc thêm một lần để không lỡ sự kiện ghi ngay trước
khi job kết thúc.
