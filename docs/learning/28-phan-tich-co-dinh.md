# 28. Phân tích cố định: model chọn, code tính

Text-to-SQL trả lời tốt câu "bao nhiêu" và "cái nào". Người dùng không làm dữ liệu còn hỏi "vì sao
giảm", "đang tăng hay giảm", "tháng sau khoảng bao nhiêu", "có gì bất thường". Bài học chung:
**để model chọn việc cần làm, còn cách làm thì giao cho code đã kiểm chứng.** (ADR 0051.)

## Hai họ giải pháp

1. **Code interpreter:** model tự viết Python, như ChatGPT Data Analysis hay Julius. Rất linh
   hoạt, nhưng mọi thứ phụ thuộc vào trình độ của model.
2. **Trusted functions:** một bộ phân tích có sẵn. Model chỉ nói "phân tích xu hướng của câu
   truy vấn này", còn code chọn phương pháp. Tableau Pulse, Power BI "Explain the increase",
   Snowflake `TOP_INSIGHTS` và ThoughtSpot đều đi theo hướng này: thống kê do code tính, LLM chỉ
   viết lời.

Nhánh `archive/python-analysis` đã thử cả hai với `gpt-oss-20b`. Khi tự viết Python, model mở
database rỗng của riêng nó, ghi đè hàm hỗ trợ và chép tay số vào code. Model lớn hơn dùng đúng
hàm nhưng chọn phương pháp ngây thơ, chẳng hạn lấy trung bình cộng làm dự báo. Với tool cố định,
model gọi đúng ở 4/4 câu, nhưng chỉ sau khi bước plan có **trường bắt buộc** ghi loại phân tích.
Model nhỏ hay bỏ qua lời dặn trong prompt, nhưng điền một enum bắt buộc thì nó làm.

## Năm loại và một loại mới

- **`trend`:** đường thẳng bình phương tối thiểu. Chỉ nói "tăng" hay "giảm" khi p < 0,05;
  ngoài ra là "không rõ xu hướng".
- **`forecast`:** làm trơn hàm mũ (ETS), có khoảng 95%. Thành phần mùa vụ chỉ bật khi có đủ hai
  mùa.
- **`correlation`:** Pearson và Spearman.
- **`outliers`:** z-score dựa trên trung vị và MAD, ít bị chính giá trị lạ kéo lệch.
- **`explain_change` (mới):** tách mức thay đổi giữa hai kỳ theo từng nhóm. Tổng các phần của
  một chiều đúng bằng mức thay đổi, nên câu trả lời có thể nói "Q1 chiếm 68% mức giảm".

## "Ở đâu" không phải "vì sao"

Phân rã cộng tính chỉ cho biết **thay đổi nằm ở đâu**. Doanh thu Q1 giảm có thể do hết hàng, do
thời tiết hay do đối thủ, và dữ liệu không nói điều đó. Card trên giao diện luôn kèm một câu cố
định: "Đây là chỗ thay đổi, không phải lý do." Khi chạy thử trên app, model vẫn viết "chủ yếu
do" trong câu trả lời của nó. Vì vậy câu cố định do code viết mới là chốt chặn, không phải lời
dặn trong prompt.

Code từ chối những trường hợp sẽ gây hiểu sai:

- **Trung bình hay tỷ lệ:** giá trị trung bình mỗi đơn không tách thành các phần cộng lại được,
  nên tool trả lỗi và model quay về SQL.
- **So nửa tháng với cả tháng:** ngày 26/11 so "tháng này" với "tháng trước" thì khoảng ngày vượt
  quá dữ liệu. Code từ chối và gợi ý so cùng số ngày (1–26/11 với 1–26/10).
- **Cắt đôi một kỳ:** nếu `x` là một dòng mỗi tháng, code không thể biết một nửa tháng.

## Gắn vào hệ thống có sẵn, không làm đường riêng

Kết quả phân tích được ghi thành một `QueryRecord`: SQL của nó, bảng kết quả làm `rows`, và thêm
trường `analysis`. Nhờ vậy mọi thứ đang đọc bằng chứng tự động áp dụng cho phân tích: kiểm tra
truy vết số, kiểm tra kỳ, kiểm tra fan-out, báo cáo HTML, phần giải thích cách tính. State của
graph không đổi, nên checkpoint cũ vẫn chạy tiếp được.

Một ràng buộc quan trọng: **khi tắt phân tích, request gửi model phải giống hệt từng byte so với
trước**. Có một test giữ mã băm của các request, nhờ đó kết quả eval cũ vẫn so sánh được.

## Đo bằng dữ liệu cài sẵn đáp án

Dữ liệu thật không cho biết "vì sao giảm" đúng hay sai. Cửa hàng giả trong `eval/analysis/` được
cài sẵn hiệu ứng: tháng 10 giảm do Q1, tháng 11 giảm do GrabFood, đồ uống tăng đều, ba ngày bất
thường. Như vậy đáp án biết trước, và việc build dataset không tốn tiền. Mỗi câu chấm hai thứ:
model có gọi đúng loại phân tích không, và con số có đúng không. Sáu câu đối chứng không cần phân
tích, dùng để bắt trường hợp model lạm dụng tool.

## Câu hỏi phỏng vấn thường gặp

- **Vì sao không để LLM tự viết code phân tích?** Model nhỏ chọn sai phương pháp và chép số. Code
  tự do không kiểm chứng được, và người dùng không đọc được code.
- **Phân rã cộng tính hoạt động thế nào? Khi nào không dùng được?** Với tổng và số đếm, các phần
  cộng lại bằng mức thay đổi. Với trung bình và tỷ lệ thì không, vì cần phân rã mix và rate.
- **Làm sao biết tool có giúp ích hay chỉ làm chậm?** Dùng bộ câu đối chứng, chạy nhiều lần
  (pass^k), và so `smoke` khi bật và khi tắt phân tích.
- **Bảo mật khi thêm thư viện vào sandbox?** Không có code nào do model viết được chạy. SQL vẫn
  qua guard. Thư viện được ghim bằng hash, và container vẫn không có mạng, chỉ đọc được đúng
  dataset của nó.
