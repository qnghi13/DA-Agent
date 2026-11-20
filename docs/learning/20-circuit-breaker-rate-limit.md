# 20. Circuit breaker và rate limit: bảo vệ agent khỏi nhà cung cấp model

Phần thứ tư của nhóm **harness**, sau pass^k ([note 19](19-pass-k.md)).

## Hai vấn đề khác nhau

1. **Nhà cung cấp đang sập.** Trước đây, mỗi request vẫn thử lại 2 lần (có thể chờ tới 30 giây)
   rồi mới chuyển sang route dự phòng. Request sau lặp lại y hệt, vì không ai "nhớ" Groq vừa lỗi.
2. **Mình gọi quá nhanh.** Groq giới hạn số request và token mỗi phút. Trước đây chỉ khi nhận 429
   mới chậm lại, và nhiều worker cùng đâm vào 429 một lúc.

## Circuit breaker: cầu dao điện

Giống cầu dao trong nhà: khi chập điện liên tục thì ngắt, để không cháy cả hệ thống.

```
closed ──(5 lỗi mạng liên tiếp)──> open ──(sau 30 s)──> half_open
  ^                                  ^                      │
  └──────(probe thành công)──────────┼──────────────────────┤
                                     └──(probe thất bại)────┘
```

- **closed:** gọi bình thường, đếm lỗi liên tiếp.
- **open:** bỏ qua route này ngay, chuyển sang route dự phòng, không chờ.
- **half_open:** cho đúng **một** request thử. Nhiều thread đến cùng lúc thì chỉ một thread được thử.

Chỉ đếm **lỗi mạng** (429, timeout, 5xx). Lỗi do request (model gọi tool sai, request quá lớn)
không nói gì về sức khỏe nhà cung cấp: nhà cung cấp vẫn trả lời, nên còn đóng được cầu dao.

**Vì sao để trong từng process?** Mỗi process tự thấy lỗi sau vài request. Dùng chung qua database
thì thêm một thứ có thể hỏng, mà lợi ích chỉ là bớt vài request lỗi. Hystrix, Resilience4j, Envoy
đều để breaker ở từng instance.

## Rate limit: token bucket

Hình dung một cái xô chứa tối đa 30 "vé" (rpm = 30), được rót thêm đều đặn 30 vé mỗi phút. Mỗi
request lấy một vé. Hết vé thì chờ. Token cũng vậy với một xô thứ hai (tpm).

Hai chi tiết quan trọng:

- **Giữ chỗ trước, cho phép âm.** Request lấy vé ngay cả khi xô đã cạn, xô thành số âm, và request
  đó chờ phần thiếu được rót lại. Request sau xếp hàng phía sau: công bằng, không ai chen ngang.
- **Không biết trước số token.** Ước lượng (số ký tự / 4 + giới hạn output), gọi xong thì
  `settle` trả lại phần ước lượng thừa.

Nếu phải chờ quá 30 giây, request không giữ chỗ mà chuyển sang route khác (`ThrottledError`).
Lỗi này là do *mình* tự chặn, nên không bao giờ làm ngắt cầu dao.

**Vì sao cần bản Postgres?** Hạn mức là của *API key*, không phải của process. Ba worker cùng
một key, mỗi cái nghĩ mình có 30 rpm, thì cộng lại thành 90. Bản Postgres để cả ba chung một xô:

- Một transaction khóa dòng "requests" rồi dòng "tokens", **luôn theo thứ tự đó**, nên hai process
  không bao giờ khóa chéo nhau (deadlock).
- Rót vé theo **đồng hồ của database**, nên đồng hồ các máy lệch nhau cũng không sao.
- Database sập thì **vẫn cho gọi** (fail open): 429 của nhà cung cấp vẫn là lưới an toàn cuối cùng.
  Fail closed thì một sự cố database sẽ dừng mọi agent.

## Phần không cần làm

"Gọi model không lặp lại khi chạy tiếp từ checkpoint": graph lưu checkpoint sau mỗi node, mỗi node
gọi model tối đa một lần. Chạy tiếp chỉ lặp lại đúng lượt gọi đang dở. Thiết kế tốt từ đầu làm một
tính năng harness trở nên thừa.

## Tự kiểm tra

1. Vì sao lỗi `InvalidToolCallError` lại *đóng* cầu dao đang half_open?
2. Ba worker, mỗi cái dùng limiter trong bộ nhớ với rpm 30 cho cùng một key. Điều gì xảy ra?
3. Vì sao khóa dòng theo thứ tự cố định tránh được deadlock?
