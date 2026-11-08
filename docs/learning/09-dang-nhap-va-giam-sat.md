# 09. Đăng nhập bằng link, cookie phiên, và dashboard vận hành

## Khái niệm

**Key cho máy, phiên cho người.** API key hợp với script và CI: gửi trong header, không bị trình
duyệt tự gửi kèm. Với người dùng trình duyệt, giữ key trong JavaScript có hai vấn đề: phải dán lại
mỗi lần mở tab, và nếu trang bị XSS thì script lạ đọc được key, mà key thì sống lâu và dùng được ở
mọi nơi. Cách phổ biến là đổi sang **cookie phiên `HttpOnly`**: JavaScript không đọc được, server
thu hồi được bất cứ lúc nào.

**Link đăng nhập dùng một lần (magic link).** Không cần mật khẩu, không cần gửi email. Lệnh admin
hoặc người đã đăng nhập tạo một link `/login#token=dal_...`:

```
admin login-link ─> /login#token=...  ─(UI POST /v1/auth/redeem)─> Set-Cookie: da_session (HttpOnly)
                     ^ phần sau # không gửi lên server: không lọt vào log nginx hay Referer
```

Token có 256 bit ngẫu nhiên, database chỉ giữ SHA-256 của nó, hết hạn sau 15 phút, và chỉ dùng được
một lần nhờ một câu `UPDATE ... WHERE used_at IS NULL AND expires_at > now() RETURNING`. Hai
request cùng lúc thì chỉ một request thắng, vì Postgres khóa dòng.

**CSRF.** Cookie được trình duyệt tự gửi, nên một trang lạ có thể khiến trình duyệt gửi POST kèm
cookie. Có hai lớp chặn: `SameSite=Strict` (trình duyệt không gửi cookie trong request khởi đầu từ
trang khác), và server kiểm tra `Origin` phải đúng địa chỉ UI với mọi request đổi dữ liệu. Request
đăng nhập cũng bị kiểm tra `Origin`, để chặn "login CSRF": trang lạ đăng nhập trình duyệt của bạn
vào workspace của kẻ tấn công.

**Hết hạn trượt có giới hạn.** Phiên hết hạn sau 30 ngày không dùng, và tối đa 90 ngày kể từ lúc
tạo. Mỗi lần dùng thì gia hạn, nhưng mỗi giờ chỉ ghi database tối đa một lần, nên phiên dùng nhiều
không tạo thêm một lần ghi cho mỗi request.

**Dashboard vận hành.** Trace (Phoenix) trả lời câu hỏi "run này đã làm gì". Dashboard trả lời
"hệ thống đang thế nào": hàng đợi có kẹt không, tỷ lệ trả lời được, p50/p95 thời gian, lần gọi
model bị lỗi theo loại, chi phí theo workspace so với ngân sách, tín hiệu chất lượng, lỗi gần đây.
Ba loại tín hiệu bổ sung cho nhau: **metrics** (số gộp, rẻ, để thấy có chuyện), **traces** (chi
tiết từng run, để tìm nguyên nhân), **logs và audit** (ai đã làm gì).

## Vì sao chọn như vậy

| Quyết định | Lý do chính | Nơi |
|---|---|---|
| Link một lần, không mật khẩu, không email | `docker compose up` vẫn chạy được mà không cần SMTP hay OAuth app | [ADR 0025](../adr/0025-browser-sessions-and-login-links.md) |
| Token sau `#` trong URL | Phần fragment không bao giờ được gửi lên server | `web/src/auth.ts` |
| Session lưu trong bảng, không dùng JWT | Thu hồi tức thì; JWT muốn thu hồi thì cũng phải có danh sách ở server | `storage/sessions.py` |
| Operator không thuộc workspace nào | Quyền tối thiểu: xem số liệu hệ thống, không xem dữ liệu khách | `api/app.py` (`identity`, `operator`) |
| Sai vai trò trả 404, không trả 403 | Không xác nhận endpoint hay tài nguyên tồn tại, giống quy tắc giữa các workspace | ADR 0020 |
| Dashboard tính trực tiếp trên bảng Postgres | Dữ liệu đã có sẵn; không thêm Prometheus hay Grafana và một chỗ đăng nhập thứ hai | [ADR 0026](../adr/0026-operator-dashboard.md) |
| Ghi cả lần gọi model bị lỗi vào ledger | Thấy được 429 và timeout ngay cả khi lần thử lại thành công | `llm/client.py` |
| Dashboard chỉ đếm lỗi verify, không hiện nội dung | Nội dung lỗi trích số liệu của khách | `storage/ops.py` |
| Màu trạng thái được validator kiểm tra, luôn có chú giải và bảng | Người mù màu vẫn đọc được; màu không phải kênh duy nhất | `OpsDashboard.tsx` |

## Phương án đã loại

- **Email + mật khẩu:** phải làm đăng ký, quên mật khẩu, băm mật khẩu, và cần máy chủ mail.
- **SSO qua oauth2-proxy:** dễ dùng nhất với người dùng, nhưng mỗi bản cài phải tạo OAuth app và
  có domain. Có thể đặt phía trước sau này; phiên vẫn nằm bên dưới.
- **Giữ key trong `localStorage`:** không mất khi đóng tab, nhưng XSS đọc được key.
- **Prometheus + Grafana:** chuẩn cho metrics hạ tầng, nhưng thêm hai service cho số liệu mà
  database đã có. Sau này có thể thêm endpoint `/metrics` mà không phải sửa trang.

## Câu hỏi phỏng vấn

**Cookie hay token trong `localStorage` cho SPA?**
Cookie `HttpOnly` + `SameSite` + kiểm tra `Origin` an toàn hơn trước XSS, vì script không đọc được
secret. Đổi lại phải chống CSRF. Token trong JavaScript thì không có CSRF nhưng mất ngay khi bị XSS.

**Magic link có những rủi ro gì?**
Link có thể bị chuyển tiếp hoặc lọt vào log. Vì vậy: dùng một lần, sống ngắn, token đặt sau `#`,
chỉ lưu hash, giới hạn số lần thử sai theo địa chỉ, và ghi audit cho mỗi lần đăng nhập.

**Giám sát một hệ thống LLM khác giám sát web service thường thế nào?**
Ngoài độ trễ và lỗi, phải theo dõi chất lượng (tỷ lệ trả lời được, abstain, check verify fail),
chi phí theo người dùng so với ngân sách, và lỗi từ provider (429, timeout) kể cả khi retry che
đi. Điểm eval chạy offline; dashboard chỉ hiện tín hiệu rẻ có sẵn trong production.

**p95 có ý nghĩa gì hơn trung bình?**
Trung bình bị vài run rất chậm kéo lệch, và lại che mất đuôi phân phối mà người dùng thật sự cảm
thấy. p50 cho trải nghiệm thường gặp, p95 cho trải nghiệm tệ mà 1 trên 20 người gặp phải.
