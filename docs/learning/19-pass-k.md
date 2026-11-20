# 19. pass^k: agent trả lời ổn định đến đâu

Phần thứ ba của nhóm **harness**, sau record/replay ([note 18](18-record-replay.md)).

## Vấn đề

Một lần chạy eval cho một con số, ví dụ +12 trên 30 câu. Nhưng chạy lại cùng code trên Groq có thể
ra +9 hoặc +15, vì model lấy mẫu hơi khác mỗi lần. Vậy khi bạn sửa prompt và thấy +14, đó là cải
thiện thật hay chỉ là may?

## Hai chỉ số

Chạy mỗi câu **k** lần:

- **pass@k**: câu đúng **ít nhất một** lần. Đo "agent *có thể* làm được không". Hay dùng cho sinh
  code, nơi người dùng thử nhiều lần.
- **pass^k** (pass-hat-k, từ benchmark tau-bench): câu đúng **cả k** lần. Đo "agent có *đáng tin*
  không". Với người dùng thật, chỉ hỏi một lần, đây mới là con số quan trọng.

Khoảng cách giữa hai con số chính là phần "chập chờn". Một agent pass@k cao nhưng pass^k thấp thì
biết cách làm nhưng không ổn định.

Thêm vào đó, **độ lệch chuẩn của điểm** giữa k lần chạy là **sàn nhiễu**: một thay đổi phải làm điểm
tăng rõ hơn mức này mới đáng tin.

## Cách làm ở đây

```bash
uv run --env-file .env python -m eval.datamate.passk --k 3 --data-dir train --set smoke \
    --output-dir eval/reports/passk-smoke -- --routes groq:openai/gpt-oss-20b \
    --max-total-tokens 2000000 --datasets-dir eval/reports/datasets-dev --agent graph --record
```

- Chạy `run.py` k lần, mỗi lần một thư mục `run-i`, nên dừng giữa chừng thì chạy lại sẽ tiếp tục.
- Chấm điểm trong process riêng, như mọi eval khác: agent không bao giờ thấy đáp án.
- vLLM với seed cố định cho k lần giống hệt nhau, nên lần i dùng seed khác (`--seed-base`).
- `--record` giữ một cassette cho mỗi lần chạy: sau này có thể replay bất kỳ lần nào với $0.

## Câu chập chờn dùng để làm gì

Câu lúc đúng lúc sai thường chỉ ra chỗ mơ hồ: quy tắc nghiệp vụ có hai cách hiểu, prompt không nói
rõ kiểu đáp án. Đó là chỗ đáng sửa hơn câu luôn sai, vì agent đã gần đúng.

## Tự kiểm tra

1. pass@3 = 0.8, pass^3 = 0.5. Điều đó nói gì về agent?
2. Vì sao chạy pass^k trên vLLM với cùng một seed là vô nghĩa?
3. Điểm trung bình +12, độ lệch chuẩn 3. Một thay đổi cho +13 trong một lần chạy có đáng tin không?
