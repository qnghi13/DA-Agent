# 22. Phản hồi người dùng, golden case và đánh giá trực tuyến

Phần thứ sáu của nhóm **harness**, sau hủy, thời hạn và time travel
([note 21](21-cancel-deadline-time-travel.md)).

## Vòng lặp còn thiếu

Trước bước này, agent được đo bằng bộ DataMate (offline) và bằng tín hiệu do code tính (verify
thất bại, tool lỗi). Không có gì cho biết **người dùng thật** thấy câu trả lời đúng hay sai.

Các hệ thống production khép vòng lặp như sau:

```
người dùng 👍/👎 ──> lưu theo run ──> 👍 thành golden case ──> chạy lại khi đổi code
                         │
                         └──> tỷ lệ 👍 theo phiên bản agent (ops dashboard)
```

LangSmith, Langfuse gọi là "feedback score" gắn vào trace, và "dataset" tạo từ trace tốt.

## Vì sao chỉ hai mức, cộng lý do

- **Hai mức** (👍/👎): quyết định một giây, nhiều người bấm, mọi người hiểu như nhau, khớp với
  "đúng/sai" của câu trả lời số liệu. Thang 1-5 thì ít người bấm, mỗi người hiểu một kiểu.
- **Lý do khi 👎** là danh sách cố định: sai số liệu, hiểu sai câu hỏi, lẽ ra phải nói không trả
  lời được, khó hiểu, khác. Mỗi lý do chỉ ra một chỗ sửa khác nhau (SQL, prompt, abstain, cách
  trình bày). Vì là từ cố định do code định nghĩa, ops dashboard **đếm được** mà không lộ chữ người
  dùng gõ. Ghi chú tự do thì giữ kín.

## Golden case

Một câu trả lời được 👍 lưu lại: câu hỏi, dataset, ngày as-of, đáp án. Khi bạn sửa prompt hay code:

```bash
python -m da_agent.goldens run --workspace W          # chỉ báo có bao nhiêu case
python -m da_agent.goldens run --workspace W --yes    # chạy lại, ghi báo cáo JSON
```

Mỗi case ra `same`, `changed`, `no_answer` hoặc `error`. Hai điểm tinh tế:

1. **So đáp án không đơn giản là `==`.** 1000 và 1000.05 là cùng một số; bảng có cột đổi thứ tự
   vẫn là cùng một bảng. Code so sánh nằm trong lõi, không dùng scorer của DataMate.
2. **`changed` nghĩa là "xem lại", không phải "sai".** Người dùng có thể 👍 nhầm một câu sai, hoặc
   dataset đã được build lại. Golden case là tín hiệu để người xem xét, không phải phán quyết.

Chạy lại dùng chính job gốc nên có đúng memory và các lượt hội thoại trước đó như lúc đầu, nhưng
không tạo job mới: lịch sử hội thoại của người dùng không bị lẫn các lần chạy thử.

## Đánh giá trực tuyến không cần LLM

Panel "User feedback" trên ops: số 👍/👎, tỷ lệ run được đánh giá, lý do 👎, và **tỷ lệ 👍 theo phiên
bản agent** (nhờ run manifest, [note 17](17-run-manifest.md)). Khi deploy phiên bản mới, so tỷ lệ
👍 của nó với phiên bản cũ là một dạng canary đơn giản.

Bước sau có thể thêm LLM chấm một mẫu run thật. Khi đó, 👍/👎 thật chính là thước đo để kiểm tra
LLM chấm có đáng tin không.

## Tự kiểm tra

1. Vì sao lý do 👎 được phép hiện trên ops dashboard còn ghi chú thì không?
2. Một golden case ra `changed` sau khi bạn sửa prompt. Bạn kiểm tra những gì trước khi kết luận
   prompt mới tệ hơn?
3. Tỷ lệ 👍 của phiên bản mới là 90%, phiên bản cũ 80%. Cần biết thêm gì trước khi tin con số này?
