# 18. Record/replay: chạy lại agent mà không gọi model

Phần thứ hai của nhóm **harness**, sau run manifest ([note 17](17-run-manifest.md)).

## Vấn đề

Khi sửa code nằm *sau* model (verify, ép kiểu đáp án, adapter DataMate), chạy eval thật có hai
nhược điểm:

1. **Tốn tiền**: mỗi lần chạy lại toàn bộ câu hỏi.
2. **Nhiễu**: model trả lời hơi khác mỗi lần, nên điểm đổi vì model chứ không phải vì code của bạn.
   Một cải tiến +3 điểm có thể bị chìm trong dao động ±5.

## Ý tưởng: cassette

Lần chạy đầu, ghi lại mọi output của model vào một file (cassette). Lần sau, thay vì gọi model,
lấy output từ file. Model coi như "đứng yên", nên mọi khác biệt về điểm là do code. Chi phí: $0.
Đây là pattern cũ của test HTTP (VCR.py), áp dụng cho LLM.

```
Ghi:      agent -> LLMClient -> RecordingAdapter -> adapter thật -> model
                                     |
                                     v ghi một dòng JSONL
Phát lại: agent -> LLMClient -> ReplayAdapter -> cassette.jsonl
```

## Các quyết định chính

- **Bọc ở adapter, không ở HTTP.** Mọi provider (Groq, vLLM, Gemini) đều qua giao diện
  `complete(request, model)`. Bọc ở đây thì retry, span, cost ledger vẫn chạy như thật, và không
  đụng đến header chứa API key ([ADR 0039](../adr/0039-record-replay.md)).
- **Khóa = hash của toàn bộ request.** Messages, tools, max tokens, temperature, cộng provider và
  model. Request giống hệt từng byte thì mới trúng. Đó là điều ta muốn: nếu code đổi thứ model
  nhìn thấy, replay phải báo, không được đoán.
- **Chỉ ghi lỗi do request gây ra.** Tool call hỏng (400) và lỗi vĩnh viễn được ghi lại vì chúng
  lặp lại với cùng request. Rate limit, timeout là chuyện mạng, không ghi; khi phát lại thì call
  thành công luôn.
- **Trượt (miss) thì dừng và chỉ chỗ lệch.** Mỗi dòng lưu hash của từng message. Khi trượt, ta tìm
  request đã ghi có phần đầu giống nhất và báo "khác từ message 4 (tool)". Nhờ đó biết thay đổi làm
  lệch hành vi từ lượt nào. Tùy chọn `--replay-miss live` gọi model thật cho phần thiếu.
- **Token giữ nguyên, tiền bằng 0.** Response phát lại vẫn mang số token cũ, nên giới hạn token mỗi
  câu hoạt động y như lần ghi. Nhưng `LLMClient` tính giá $0 vì cờ `replayed`.

## Cách dùng

```bash
# Ghi (gọi model thật, tốn tiền như bình thường)
uv run python -m eval.datamate.run ... --output-dir eval/reports/a --record
# Sửa code sau model, rồi phát lại ($0)
uv run python -m eval.datamate.run ... --output-dir eval/reports/b --replay eval/reports/a
```

`metrics.json` của lần phát lại có `replay.hits`, `replay.misses` và chi tiết chỗ lệch.

## Giới hạn

- Không đo được prompt mới: prompt đổi thì mọi câu đều trượt. Việc đó cần eval thật.
- Không đo độ dao động của model: đó là việc của pass^k (bước sau).
- Cassette chứa câu hỏi và dữ liệu, nên không commit (luật của ban tổ chức).

## Tự kiểm tra

1. Vì sao replay giữ số token nhưng tính tiền bằng 0?
2. Bạn sửa hàm ép kiểu đáp án. Replay có trượt không? Còn khi sửa system prompt?
3. Vì sao không ghi lỗi timeout vào cassette?
