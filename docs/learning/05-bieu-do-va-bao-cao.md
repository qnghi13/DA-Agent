# 05. Biểu đồ, báo cáo và truy nguồn con số

## Khái niệm

**Biểu đồ và báo cáo cũng là đầu ra của model, nên cũng có thể sai hoặc nguy hiểm.** Một spec
Vega-Lite do model viết có thể chứa `data.url` (tải dữ liệu từ bất kỳ đâu), có thể chứa expression
chạy trong trình duyệt người xem, hoặc chứa con số mà không query nào trả về. Một báo cáo HTML hiển
thị chữ của model và giá trị trong file người dùng tải lên, nên nó là một bề mặt injection.

**Code dựng spec, model chỉ chọn.** `make_chart` nhận một câu SQL, loại mark (`bar`, `line`, `point`,
`area`, `arc`) và tên các cột `x`, `y`, `color`. Code chạy query, rồi tự dựng spec gồm các dòng kết
quả, encoding và kiểu trường đọc từ giá trị. Mọi key khác model gửi lên đều bị bỏ qua. Nhờ vậy một
spec không thể tải dữ liệu, không thể chạy expression, và mọi giá trị trong biểu đồ đều đến từ query.
Model chỉ nhận lại `{"status": "drawn", ...}` chứ không nhận các dòng, nên số trong câu trả lời vẫn
phải đến từ `run_sql` và đi qua verify như trước.

**Truy nguồn đến từng ô.** Báo cáo link mỗi con số trong câu trả lời tới đúng ô kết quả đã tạo ra
nó, ví dụ `#q2-r1-c2` là query 2, hàng 1, cột 2. Phép so khớp dùng cùng quy tắc làm tròn với verify.
Dữ liệu là đúng các dòng model đã thấy, lấy từ messages của run, không chạy lại query. Số trong phần
giải thích cũng được link nếu có query trả về số đó, đọc được cả kiểu `1,234.5` lẫn `1.234,5`.

**Phòng thủ nhiều lớp trong HTML.** Mọi text đều được escape. Spec nằm trong block
`application/json` với `<`, `>`, `&` đã escape, nên `</script>` trong dữ liệu không đóng được block.
Content Security Policy chỉ cho phép bốn thư viện Vega ghim version, kiểm bằng SRI hash, cộng đúng
một inline script theo hash của nó. Không có `unsafe-eval`, vì Vega chạy qua `vega-interpreter`
thay vì sinh code bằng `new Function`.

## Vì sao chọn như vậy

| Quyết định | Lý do chính | Nơi |
|---|---|---|
| Model không viết Vega-Lite | Schema Vega-Lite cho phép URL và expression; kiểm từng phần khó hơn tự dựng | [ADR 0015](../adr/0015-charts-built-by-code.md) |
| Spec lưu trên `ToolStep` cùng SQL | Biểu đồ giữ bằng chứng, resume không vẽ lại, checkpoint cũ vẫn load | `agent/tools.py` |
| `make_chart` chỉ bật khi run yêu cầu | Prompt eval không đổi, điểm vẫn so được với baseline | `agent/graph.py` |
| Báo cáo lấy dòng từ messages | Không cần dataset hay sandbox, và đúng là dữ liệu câu trả lời đã dùng | [ADR 0016](../adr/0016-html-reports.md) |
| Ghim `vega-interpreter@1.0.5` | Bản 1.2.1 vẽ ra SVG rỗng không báo lỗi; bản 2.x chỉ là ES module | `reports/html.py` |

**Kiểm tra bằng trình duyệt thật.** Unit test chứng minh escape, CSP và link, nhưng không chứng minh
được biểu đồ vẽ ra. Chạy Chrome headless (`--dump-dom`, rồi chụp màn hình) mới lộ ra lỗi
`vega-interpreter@1.2.1`: trang không có lỗi nào hiện ra, chỉ có một SVG trống. Bật log console và
thử từng biến thể (bỏ CSP, bỏ interpreter, đổi version) để khoanh vùng nguyên nhân.

**Thử nghiệm RAG đã cắt.** Plan yêu cầu so retrieval với việc đưa cả rulebook vào prompt. Số liệu đã
có trả lời câu đó: khi bước plan chỉ thấy tiêu đề rule và phải tự tìm bằng `search_rules`, điểm là +2
trên `bootstrap`; khi plan đọc toàn bộ rule, điểm là +11. Lỗi gốc là model không biết mình cần tra
rule, chứ không phải tìm sai section ("no section matches" khoảng 1 lần mỗi 30 câu). Retrieval bằng
embedding vẫn phụ thuộc vào việc model chủ động hỏi, nên không sửa được lỗi này. Rulebook khoảng
4,700 token vẫn vừa trong một lần gọi plan.

## Phương án đã loại

- **Model viết spec đầy đủ, code kiểm bằng JSON schema:** schema vẫn cho phép URL và expression, và
  số inline cần một kiểm tra truy nguồn riêng.
- **Vẽ bằng matplotlib trong sandbox:** linh hoạt hơn nhưng là chạy code, ra ảnh không kiểm được, và
  mất tooltip.
- **Chạy lại query khi dựng báo cáo:** cần dataset và sandbox, và có thể ra dòng khác với dòng câu
  trả lời đã dùng.
- **Nhúng thư viện Vega vào file (khoảng 830 KB):** chạy offline được nhưng nặng. Không có mạng thì
  báo cáo vẫn hiện dữ liệu và query của từng biểu đồ.
- **RAG bằng embedding:** thêm một model và một store cho khoảng 10 section, mà không sửa được lỗi gốc.

## Câu hỏi phỏng vấn

**Làm sao để biểu đồ do LLM tạo ra không nói dối?**
Không cho model viết dữ liệu của biểu đồ. Model viết query, code chạy query và dựng spec, nên biểu đồ
chỉ có thể sai khi chính query sai. Query đó được lưu cùng spec để người xem kiểm tra.

**Báo cáo HTML chứa dữ liệu không tin cậy: chặn XSS thế nào?**
Escape mọi text. Dữ liệu cho JavaScript đặt trong block JSON trơ và escape `<`. Thêm CSP như lớp thứ
hai: `default-src 'none'`, script chỉ từ CDN có SRI và inline script theo hash, không `unsafe-eval`.
Một test chèn `</script>` và `<img onerror>` vào dữ liệu, tiêu đề và câu hỏi.

**"Mọi con số đều truy được nguồn" nghĩa là gì trong thực tế?**
Mỗi số trong câu trả lời là một link tới đúng ô kết quả query, theo cùng quy tắc làm tròn với bước
verify. Số không khớp ô nào được đánh dấu và đếm, thay vì bị giấu đi.

**Khi nào nên dùng RAG?**
Khi tài liệu quá lớn để đưa hết vào prompt, hoặc khi chi phí token là vấn đề chính. Trước khi thêm
retrieval, hãy đo xem lỗi thật nằm ở đâu. Ở đây lỗi là model không biết cần tra rule, nên đưa toàn bộ
rule vào bước plan hiệu quả hơn mọi cách tìm kiếm.
