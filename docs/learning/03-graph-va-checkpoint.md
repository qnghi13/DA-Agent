# 03. Graph và checkpoint

## Khái niệm

**Graph thay cho vòng lặp.** Vòng lặp tối giản là một hàm: gọi model, chạy tool, lặp lại tới khi
có câu trả lời. Nó chạy được, nhưng không dừng lại để hỏi người dùng được, chết giữa chừng là mất
hết, và nộp nguyên câu trả lời model đưa ra. Graph chia công việc thành các node, mỗi node làm một
việc, còn code quyết định đi tiếp sang node nào:

```
plan ─┬─ mơ hồ và có người để hỏi ─> clarify (dừng lại chờ) ─> plan
      └─> agent ⇄ tools ─(nộp câu trả lời)─> verify ─┬─ đạt ─────────────> answer
            ^                                       ├─ chưa đạt, còn lượt ┘
            └───────────────────────────────────────└─ chưa đạt, hết lượt ─> abstain
```

**State và reducer.** State là dữ liệu đi qua graph. Mỗi node trả về phần state nó muốn đổi. Với
trường kiểu danh sách như `messages` hay `steps`, reducer `operator.add` nối phần mới vào thay vì
ghi đè. Nếu quên reducer, mỗi node sẽ xoá lịch sử của node trước.

**Checkpoint.** Sau mỗi node, LangGraph lưu state vào Postgres. Khi worker chết, worker khác đọc
checkpoint gần nhất và chạy tiếp từ node đang dở. Vì một node có thể bị chạy lại, node đó phải an
toàn khi chạy hai lần: tool chỉ đọc dữ liệu, và mỗi lượt gọi model là một node riêng, nên chạy lại
chỉ lặp tối đa một lượt.

**Interrupt.** Node `clarify` gọi `interrupt()`: graph lưu checkpoint rồi dừng. Khi người dùng trả
lời, `Command(resume=...)` đưa câu trả lời vào đúng chỗ đã dừng. Graph không giữ tiến trình nào
trong lúc chờ, nên người dùng trả lời sau một phút hay một ngày đều được.

## Vì sao chọn như vậy

| Quyết định | Lý do chính | ADR |
|---|---|---|
| Gộp "hiểu câu hỏi" và "lập kế hoạch" vào một lần gọi | Hai việc đọc cùng một đầu vào; tách ra là trả tiền token hai lần | [0013](../adr/0013-agent-graph.md) |
| Mỗi lượt model là một node | Chạy tiếp sau sự cố chỉ lặp lại tối đa một lượt | [0013](../adr/0013-agent-graph.md) |
| Chỉ hỏi lại người dùng khi chạy tương tác | Lượt eval không có ai trả lời; tài liệu đã có quy tắc mặc định | [0013](../adr/0013-agent-graph.md) |
| State chỉ chứa dữ liệu thuần | Lưu và đọc lại được; dataset và model client nằm trong runtime context | [0013](../adr/0013-agent-graph.md) |
| Lỗi nhà cung cấp không đóng run | Lỗi mạng không nói gì về câu hỏi; lần sau thử lại từ checkpoint | [0013](../adr/0013-agent-graph.md) |
| `verify` bằng code | Rẻ, test được; con số không có trong kết quả truy vấn thì không được nộp | [0007](../adr/0007-code-first-verification.md) |

**Kết quả đo** (đúng / sai / bỏ trống, cùng 90 câu):

| Bộ câu hỏi | Vòng lặp | Graph |
|---|---|---|
| `bootstrap` | 18 / 4 / 8, +14 | 18 / 7 / 5, +11 |
| 5 shop dòng tổng | 18 / 8 / 4, +10 | 16 / 7 / 7, +9 |
| `smoke` | 13 / 6 / 11, +7 | 18 / 7 / 5, +11 |
| **Tổng** | **+31** | **+31** |

Graph không làm tăng điểm, nhưng cũng không làm giảm, và có thêm khả năng chạy tiếp sau sự cố, hỏi
lại người dùng và kiểm tra câu trả lời. Giá phải trả là khoảng 13% token nhiều hơn.

**Bài học lớn nhất: kế hoạch sai còn hại hơn không có kế hoạch.** Bản graph đầu tiên chỉ đạt +2
trên `bootstrap`. Node `plan` chỉ thấy tiêu đề các mục của rulebook, nên tự đoán "doanh thu" là cột
tổng thanh toán (gồm cả phí ship và thuế). Agent làm theo kế hoạch đó thay vì tự tra định nghĩa như
vòng lặp cũ. Cho `plan` đọc toàn bộ tài liệu, chỉ trong một lần gọi duy nhất, thì điểm lên +11.

## Phương án đã loại

- **Cả vòng tool là một node:** đơn giản hơn, nhưng chạy tiếp sau sự cố sẽ gọi lại mọi lượt model.
- **Để `plan` quyết định "thiếu dữ liệu" rồi bỏ trống luôn:** đó là đoán trước khi truy vấn, sẽ bỏ
  trống cả những câu trả lời được. Vòng tool tự kiểm tra rồi mới nộp `unanswerable`.
- **Cho model tự chấm câu trả lời của mình:** dễ mắc lại đúng lỗi của chính nó.
- **Bộ 150 câu cho mọi thay đổi:** tốn khoảng $0.40 mỗi lần. Với ngân sách nhỏ, mỗi thay đổi chỉ đo
  trên bộ 30 câu liên quan.

## Câu hỏi phỏng vấn

**Worker chết giữa chừng thì sao?**
Mỗi node xong là một checkpoint trong Postgres. Gọi lại với cùng `thread_id`, LangGraph chạy tiếp từ
node đang dở. Có test kill run ở một kết nối rồi hoàn tất ở kết nối khác, và đếm số lượt gọi model
để chứng minh không lượt nào bị gọi lại.

**Vì sao không để dataset hay model client trong state?**
State phải lưu được vào database và đọc lại ở tiến trình khác. Kết nối và client thì không lưu được.
Chúng nằm trong runtime context và được tạo lại mỗi lần chạy, còn state chỉ giữ id và dữ liệu thuần.

**Thêm một node `plan` có đáng không, nếu điểm không tăng?**
Đáng ở chỗ khác: `plan` là nơi phát hiện câu hỏi mơ hồ để hỏi lại người dùng, và đưa schema vào trước
nên vòng tool bớt một lượt mô tả dữ liệu. Nhưng phải đo, vì lần đầu nó làm điểm giảm từ +14 xuống +2.

**Lỗi mạng khác lỗi của model ở chỗ nào?**
Lỗi mạng hay hết quota không nói gì về câu hỏi, nên run được giữ mở để thử lại. Lỗi do model, như gọi
tool sai tên, tốn một lượt và model được nhắc sửa, trong giới hạn số lượt.
