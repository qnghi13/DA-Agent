# 14. Tổng quan dữ liệu: profile từng cột

## Khái niệm

**Data profiling** là bước đầu tiên khi DA nhận một bộ dữ liệu lạ. Với mỗi cột, họ muốn biết:

| Câu hỏi | Chỉ số |
|---|---|
| Cột này kiểu gì? | type (text, number, money, date…) |
| Thiếu bao nhiêu? | % ô có giá trị (filled) |
| Có ô nào không đọc được không? | số ô parse lỗi, kèm vài ví dụ |
| Có bao nhiêu giá trị khác nhau? | distinct |
| Giá trị nằm trong khoảng nào? | min, max, mean (số); từ ngày → đến ngày (ngày) |
| Giá trị nào hay gặp? | 5 giá trị nhiều nhất kèm số dòng (text, boolean) |

Profile giúp giải thích vì sao một con số có thể lệch. Ví dụ cột `status` có cả `completed`
(126 dòng) lẫn `Completed` (23 dòng): lọc `status = 'completed'` sẽ bỏ sót 23 đơn. Người dùng thấy
điều này trong profile, rồi ghi chú vào Memory cho agent.

## Quyết định thiết kế

- **Tính lúc build, không tính khi xem.** Build đã chạy trong sandbox với bảng sạch đang mở, nên
  thêm vài phép gộp (`count(DISTINCT)`, `min`, `max`, `avg`, `GROUP BY … LIMIT 5`) là rẻ. Dữ liệu
  không đổi sau khi build, nên không cần job chạy lại mỗi lần xem
  ([ADR 0034](../adr/0034-column-profile-at-build.md)).
- **Tách profile cho người khỏi dữ liệu cho agent.** Prompt của agent đọc `min_value`,
  `max_value`, `values`. Nếu đổi nghĩa các trường đó, ví dụ cho `min_value` chứa cả số, thì câu
  trả lời của agent thay đổi và phải chạy lại eval. Vì vậy profile dùng trường mới.
- **Cột ẩn không bao giờ được profile.** Cột nhạy cảm bị bỏ khỏi bảng sạch, nên không thể lộ qua
  min, max hay giá trị hay gặp.

## Những điều rút ra

- **Phần trăm không được làm tròn lên 100%.** 9.999/10.000 ô có giá trị phải hiện 99,9%, không phải
  100%, nếu không người dùng tưởng cột đầy đủ.
- **Cột ID cần cách hiện riêng.** Liệt kê "DH0001 (1), DH0002 (1)…" là nhiễu; câu "no value
  repeats" nói đúng điều cần biết.
- **Dataset build trước đó không có profile** cho đến khi tải lại, vì hiện chưa có cách build lại
  một dataset đã sẵn sàng.
