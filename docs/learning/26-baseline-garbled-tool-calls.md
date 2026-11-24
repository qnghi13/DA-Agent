# 26. Baseline one-shot và lệnh gọi tool bị hỏng

Hai việc nối tiếp nhau: đo agent so với một cách làm đơn giản (ADR 0048), rồi sửa lỗi mà phép đo
đó làm lộ ra (ADR 0049). Bài học chung: **đo trước khi tin, xem dữ liệu trước khi sửa.**

## Vì sao cần baseline

"+53 trên 150 câu" không nói lên gì nếu không có mốc so sánh. Câu hỏi người ngoài sẽ hỏi: *chỉ
cần một prompt text-to-SQL thì được bao nhiêu?* Baseline trả lời câu đó.

Thiết kế baseline phải **công bằng**, nếu không con số vô nghĩa:

- **Cùng đầu vào:** cùng dataset đã chuẩn bị, cùng rulebook (đưa trọn vào prompt vì baseline
  không có `search_rules`), cùng `run_sql` và `submit_answer`, cùng kiểu câu trả lời.
- **Bỏ đúng những gì agent thêm vào:** khám phá dữ liệu, nhiều query, thử lại khi SQL lỗi, plan,
  verify, repair. Còn lại hai lần gọi model: viết một query, rồi trả lời từ kết quả.
- **Phương án đã bỏ:** chỉ cho model viết SQL rồi code tự đổi kết quả thành câu trả lời. Phần lớn
  câu hỏi cần kiểu (percent, date, list) mà bảng kết quả không mang, nên baseline đó chỉ đo việc
  định dạng.

## Đọc kết quả: phủ rộng hay chính xác?

Agent +61, baseline +40. Nhìn sâu hơn con số tổng:

| | Trả lời | Đúng trong số đã trả lời |
|---|---|---|
| Baseline | 112 câu | 68% |
| Agent | 135 câu | 73% |

Agent hơn chủ yếu vì **trả lời được nhiều câu hơn**, không phải vì trả lời đúng hơn nhiều. Dữ
liệu sạch cộng rulebook trong prompt đã đi được 2/3 chặng đường. Điều này khớp với bài học cũ:
chuẩn bị dữ liệu quan trọng hơn thiết kế agent (ADR 0012, `docs/eval/README.md`).

**Nhiễu:** từ pass^k (note [19](19-pass-k.md)), hai lần chạy 150 câu lệch nhau khoảng 10 điểm chỉ do ngẫu nhiên.
+21 là khoảng 2 lần mức đó: đáng tin. +6 (sau khi sửa lỗi) thì không: phải nói "trong nhiễu".

## Chẩn đoán: tôi đã đoán sai, rồi lại đoán sai

1. Thấy baseline bỏ trống nhiều ở lượt trả lời, tôi đoán: *model muốn chạy thêm query.*
2. Đếm 107 lỗi trong các cassette cũ: 61% là `submit_answer<|channel|>commentary`, tức tên tool
   bị hỏng. Tôi đổi ý: *chắc baseline cũng bị lỗi tên.*
3. Chạy lại có ghi `failed_generation`: 23/24 lỗi của baseline đúng là `run_sql`. Giả thuyết đầu
   đúng với baseline; kiểu tên hỏng chỉ xảy ra trong hội thoại nhiều lượt của agent.

Bài học: **mỗi giả thuyết cần dữ liệu riêng của nó.** Số liệu từ agent không trả lời được câu hỏi
về baseline. Và nếu lỗi không được ghi lại (code cũ bỏ `failed_generation`), không ai chẩn đoán
được: ghi lại nội dung lỗi là bước đầu tiên của mọi bản sửa.

## Vì sao tên tool bị hỏng

`gpt-oss` được huấn luyện với định dạng **harmony**: output chia thành các kênh (`analysis`,
`commentary`, `final`), đánh dấu bằng token như `<|channel|>`. Lệnh gọi tool nằm ở kênh
`commentary`. Đôi khi model để token đó dính vào tên tool. Groq kiểm tra tên ở phía server và trả
400, kèm output thật của model trong `failed_generation`.

## Sửa bằng code, có giới hạn

- **Đọc lại tên:** bỏ phần từ `<|` trở đi và tiền tố `functions.`. Nếu kết quả **đúng là** một tool
  đang được phép gọi ở lượt này và tham số là JSON object, coi như model gọi tool đó.
- **Không đoán thêm:** `json`, `answer` có thể là nhiều thứ, nên để nguyên là lỗi. Tool mà code
  không cho phép ở lượt này (như `run_sql` ở lượt cuối) **không bao giờ** được trả về: sửa tên
  không được thành đường vòng qua giới hạn của code.
- **An toàn:** tham số vẫn qua mọi kiểm tra của tool (SQL guard, `parse_answer`), như mọi lệnh gọi
  khác. Lỗi được sửa có đánh dấu trên span để operator thấy.
- **Thử lại một lần trong lượt:** lỗi lần đầu được hỏi lại ngay kèm thông báo, không mất lượt. Lần
  thứ hai thì mất lượt như cũ, nên vòng lặp vẫn có giới hạn.

## Đo cơ chế, không chỉ đo điểm

Điểm +61 → +67 nằm trong nhiễu, nhưng **cơ chế** đo được trực tiếp: 64 lệnh gọi được sửa, số câu
dừng vì lỗi 7 → 2, ít hơn 50 lần gọi model, rẻ hơn 15%, nhanh hơn 2,6 giây mỗi câu. Khi điểm không
đủ nhạy, hãy đo thứ mà thay đổi tác động trực tiếp.
