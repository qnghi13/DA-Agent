# 23. SLO và cảnh báo: Prometheus và Alertmanager

Phần cuối của nhóm **harness**, sau phản hồi người dùng và golden case
([note 22](22-feedback-goldens.md)).

## Từ dashboard sang cảnh báo

Dashboard trả lời câu hỏi "hệ thống thế nào?" **khi có người mở nó**. Cảnh báo trả lời câu hỏi đó
**khi không ai nhìn**. Để cảnh báo không vô nghĩa (báo quá nhiều thì người ta tắt đi), cần bốn
khái niệm của SRE:

| Khái niệm | Ở DA-Agent |
|---|---|
| **SLI**: con số đo chất lượng | % run không lỗi, % run xong dưới 60 s, % job chờ dưới 30 s, % request API không 5xx |
| **SLO**: mục tiêu cho SLI | 99%, 95%, 99%, 99.5% trong 7 ngày |
| **Error budget**: phần được phép hỏng | 1%, 5%, 1%, 0.5% |
| **Burn rate**: tốc độ tiêu budget | 1 = tiêu vừa đủ trong 7 ngày; 14.4 = hết trong ~12 giờ |

## Cảnh báo theo burn rate, hai cửa sổ

Không cảnh báo khi "có lỗi", vì luôn có vài lỗi. Cảnh báo khi **budget bị tiêu quá nhanh**:

- **page** (gọi người dậy): burn rate > 14.4 trong 1 giờ **và** trong 5 phút.
- **ticket** (xử lý trong giờ làm): burn rate > 6 trong 6 giờ **và** trong 30 phút.

Cửa sổ dài chứng minh vấn đề có thật; cửa sổ ngắn làm cảnh báo **tắt nhanh** khi vấn đề hết. Thêm
điều kiện **số sự kiện tối thiểu**: hệ thống ít traffic, một run lỗi trong 2 run là 50% lỗi, nhưng
không đáng gọi ai dậy. Lúc viết test, test "ít traffic" đầu tiên pass vì lý do sai (cửa sổ 5 phút
không có dữ liệu); làm hỏng rule có chủ đích mới thấy test yếu. Bài học: **test phải fail được**.

## Metric lấy từ đâu

```
worker, api ──/metrics:9100──> Prometheus ──rule──> Alertmanager ──webhook──> Slack/Telegram
     │                             │
     └── đếm trong code             └── PromQL: SLI, burn rate
api ── đọc Postgres (hàng đợi, budget, 👍/👎), cache 30 s
```

- **Kết quả và độ trễ của run** đếm ngay trong worker. Lý do quan trọng nhất: **giám sát không được
  hỏng cùng thứ nó giám sát**. Nếu đếm từ Postgres, khi Postgres sập thì metric cũng tắt, đúng lúc
  cần cảnh báo nhất.
- **Hàng đợi, budget, phản hồi** vốn nằm trong Postgres, nên đọc từ đó. Đây là cách các hệ hàng đợi
  (Celery, Sidekiq) vẫn làm.
- Nhãn metric chỉ là từ cố định, tên model, mẫu route (`/v1/runs/{run_id}`, không phải id thật):
  không bao giờ chữ người dùng, và số chuỗi thời gian không tăng theo id.

## Alertmanager làm gì mà tự viết thì mệt

Gom các cảnh báo giống nhau thành một tin, không gửi trùng, **inhibit** (worker sập thì đừng báo
thêm "hàng đợi ùn", vì đó là hệ quả), silence khi bảo trì, gửi tin "đã hết sự cố". Thử thật: dừng
worker, sau ~3 phút webhook nhận `WorkerDown`; bật lại, ~5 phút sau nhận "resolved".

## Tự kiểm tra

1. Vì sao cảnh báo cần cả cửa sổ 1 giờ và 5 phút, không chỉ một?
2. Run bị người dùng hủy có tính vào error budget không? Vì sao?
3. Nếu chỉ dùng metric đọc từ Postgres, kịch bản nào làm bạn không nhận được cảnh báo?
