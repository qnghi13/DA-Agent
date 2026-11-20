# 21. Hủy, thời hạn và time travel: điều khiển một run đang chạy

Phần thứ năm của nhóm **harness**, sau circuit breaker và rate limit
([note 20](20-circuit-breaker-rate-limit.md)).

## Vì sao không "giết" thread được

Python không cho dừng một thread từ bên ngoài. Kể cả ngôn ngữ cho phép, giết giữa chừng có thể để
lại kết nối database dở dang hoặc checkpoint ghi một nửa. Cách chuẩn là **dừng hợp tác**
(cooperative cancellation): run tự hỏi "mình có nên dừng không?" ở những điểm an toàn.

```
người dùng bấm Stop ─┐
hết thời hạn ────────┼──> StopToken ──> kiểm tra trước mỗi lượt gọi model
mất lease ───────────┘                  và giữa các node của graph
```

Điểm an toàn ở đây là **trước mỗi lượt gọi model** và **giữa các node**: lúc đó checkpoint vừa được
ghi, nên dừng không làm mất gì.

## Ba nguồn dừng

| Nguồn | Phát hiện thế nào | Kết thúc |
|---|---|---|
| Người dùng hủy | API ghi `cancel_requested_at`; run hỏi database mỗi giây | job `cancelled` |
| Hết thời hạn | đồng hồ trong token, mặc định 300 giây | job thành công, `deadline_exceeded` |
| Mất lease | heartbeat đặt cờ `lost` | dừng, không ghi kết quả |

Vì sao hết thời hạn lại là "thành công"? Giống hết budget: đó là kết quả của run, không phải lỗi hệ
thống. Nếu coi là lỗi, hàng đợi sẽ **thử lại** và tốn tiền thêm lần nữa cho đúng câu hỏi đó.

## Bài học từ lần chạy thật

Lần bấm Stop đầu tiên mất 19 giây. Lý do: run đang **chờ rate limiter**
([note 20](20-circuit-breaker-rate-limit.md)), và lúc ngủ thì không ai hỏi token. Route cuối có
thể chờ tới 300 giây, nên trường hợp xấu nhất là 5 phút. Sửa: mọi lúc chờ trong `LLMClient` ngủ
từng đoạn 1 giây và hỏi token sau mỗi đoạn.

Bài học chung: **mọi chỗ chờ lâu đều phải hủy được**. Tính năng này (hủy) va chạm với tính năng
trước (chờ quota), và chỉ chạy thật mới thấy.

## Time travel

LangGraph đã lưu checkpoint sau mỗi node. Công cụ chỉ cần đọc chúng:

```bash
python -m da_agent.timetravel list RUN_ID            # các bước: node nào đã chạy, node nào kế tiếp
python -m da_agent.timetravel show RUN_ID CKPT_ID    # state tại một bước
python -m da_agent.timetravel fork RUN_ID CKPT_ID    # chạy tiếp từ bước đó bằng code hiện tại
```

**Fork** chép một checkpoint sang thread mới rồi chạy tiếp. Hai bẫy đã gặp:

1. Fork trên chính thread cũ thì bản fork thành "mới nhất", và mọi chỗ đọc run gốc sẽ thấy nó.
2. Checkpoint "sau bước plan" còn giữ **kết quả của bước kế tiếp** dưới dạng pending writes. Chép
   cả chúng thì LangGraph coi bước đó đã xong và không chạy lại. Fork phải bỏ chúng đi.

Dùng khi nào: một run trả lời sai ở bước 4. Bạn sửa prompt hoặc code, rồi fork từ bước 3 để xem
bước 4 giờ ra sao, mà không phải chạy lại từ đầu.

## Tự kiểm tra

1. Vì sao `deadline_exceeded` không làm job thất bại?
2. Bấm Stop lúc run đang đợi một lượt gọi model 40 giây. Bao lâu thì run dừng? Vì sao?
3. Vì sao fork không chép pending writes?
