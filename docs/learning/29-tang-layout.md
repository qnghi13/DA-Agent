# 29. Tầng layout: đọc bố cục sheet thành bảng gọn

Dữ liệu thật hiếm khi là "một dòng header rồi đến các bản ghi". Có header hai ba tầng, cột là ngày
1–31, tên lớp và môn nằm trong tiêu đề, hai bảng nằm cạnh nhau, các dòng `khoá: giá trị` chạy dọc
một cột, ô lịch chứa nhiều đơn hàng. Bài học chung: **code tìm cấu trúc, model chọn phép biến
đổi từ một bộ cố định, code áp dụng và chấm kết quả.** (ADR 0052.)

## Vì sao không để model tự đọc sheet

- **Model kém ở việc tìm ranh giới bảng.** Trong SpreadsheetLLM, GPT-4 chỉ đạt F1 khoảng 0,15
  đến 0,41 khi tìm bảng trong lưới ô thô. Code làm việc này tốt hơn: gom các ô có dữ liệu liền
  nhau thành vùng (connected components), như Unstructured và Docling.
- **Model kém ở việc viết code tự do.** ADR 0051 đã thấy `gpt-oss-20b` dùng sai Python tự do.
  Auto-Tables (Microsoft) mô tả việc đổi hình dạng bảng bằng 8 phép biến đổi cố định, rồi chọn
  phương án tốt nhất bằng cách kiểm tra đầu ra. Ta làm theo hướng đó.

## Ba bước

1. **Code đọc lưới ô.**
   - Tìm vùng bảng, rồi gán vai trò cho từng dòng: tiêu đề, các tầng header, section, dòng tổng,
     chân trang, header lặp lại sau ngắt trang, bản ghi.
   - Gom các sheet có header giống nhau thành **họ** (bỏ qua hoa thường, dấu, thứ tự cột).
   - Gắn **cờ** cho họ nào không phải "một header rồi bản ghi". Chỉ họ có cờ mới tốn một lệnh gọi
     model. 24 shop cũ không họ nào có cờ, nên bảng giữ nguyên và không tốn đồng nào.
2. **Model chọn phép biến đổi.**
   - Model đọc một bản xem trước có giới hạn kích thước, đã che số điện thoại và các giá trị nhạy
     cảm, rồi trả về một `LayoutSpec` dạng JSON.
   - Spec gồm: vùng nào, những dòng nào là header, context lấy từ đâu (tên sheet, một ô, ô sau một
     nhãn), và phép biến đổi: `unpivot` (cột ngày thành dòng), `split_groups` (hai học kỳ cạnh
     nhau thành dòng), `pair_rows` (dòng Vào/Ra), `record_blocks` (dòng `khoá: giá trị`),
     `split_cell` (ô chứa nhiều bản ghi).
   - Spec không chứa code hay regex, và kích thước bị giới hạn.
3. **Code áp dụng và chấm.** Model đưa 2 phương án, mỗi phương án được ingest thật, rồi chọn
   phương án tốt nhất. Nếu còn lỗi thì gửi lại cho model sửa, tối đa 2 vòng.

## Coverage chưa đủ: phải kiểm tra hình dạng

- **Coverage** hỏi: mọi ô có dữ liệu có đi đúng một chỗ không? Ô đó có thể vào bản ghi, header,
  context, hoặc dòng bị loại.
- **Lần chạy đầu cho thấy coverage bị mù.** Ba lỗi khác nhau đều được chấm 1,0:
  - lấy ô nhãn "Lớp" làm giá trị lớp;
  - để 31 cột ngày nằm ngang;
  - một phép biến đổi thất bại nhưng các dòng gốc vẫn đi qua.

  Mọi ô đều "được dùng", nhưng bảng không gọn.
- **Cách sửa là để code tự kiểm tra hình dạng đầu ra:**
  - có cột 1, 2, 3… nằm ngang không;
  - có cùng các cột con lặp dưới nhiều nhãn nhóm không;
  - có cột toàn dòng `khoá: giá trị` không;
  - ô context có đứng yên trong khi ô bên phải nó lại thay đổi giữa các sheet không.

  Mỗi lỗi bị trừ điểm và được báo lại cho model bằng lời.
- **Các phép biến đổi phải từ chối thay vì lặng lẽ cho qua.** Ví dụ: khoá lặp lại trong một bản
  ghi (lúc đầu giá trị sau bị mất mà không báo), hoặc chia nhóm trên chính dòng tên cột.
- **Kết quả trên dev:** từ 41/49 lên 48/49 target, bằng trần của layout viết tay.

## Đo cho trung thực

- **Không có đáp án câu hỏi**, nên ta viết **target bố cục** bằng tay. Mỗi target gồm số dòng
  mong đợi và vài nhóm giá trị phải cùng nằm trên một dòng. So theo giá trị chứ không theo tên
  cột, nên model đặt tên cột kiểu gì cũng được.
- **Trước khi tốn tiền**, ta đo hai mốc: baseline (code hiện tại, 30/49) và trần (layout viết tay,
  48/49). Trần cho biết bộ phép biến đổi có đủ hay không. Nếu trần thấp thì có chỉnh prompt bao
  nhiêu cũng vô ích.
- **Kết quả dao động giữa các lần chạy** (lần 3 đến lần 5 lệch nhau 3 target), nên code cuối được
  đo hai lần.
- **Held-out chỉ chạy một lần**, với target viết trước khi chạy: 33 lên 38/47. Thấp hơn dev là
  điều tập held-out sinh ra để cho thấy. Hai phép biến đổi chỉ held-out mới cần (`pair_rows`,
  `split_cell`) chưa lần nào thành công, dù test tổng hợp đều qua. Unit test chứng minh code đúng
  đặc tả, chưa chứng minh model biết dùng nó. Không chỉnh theo held-out: những bố cục này sẽ được
  đưa vào dev bằng ví dụ tổng hợp.

## Chi tiết kỹ thuật đáng nhớ

- **`_row` và `_cell`.** Build cần `_row` duy nhất và tăng dần trong mỗi nguồn (cho fill-down, so
  trùng lặp, cách ly giá trị lỗi). Vì vậy bảng làm từ layout dùng `_row` là số thứ tự bản ghi, và
  thêm cột `_cell` ghi vùng ô gốc (`A5:J5`) để truy lại.
- **Layout nằm trong spec đã duyệt.** Sản phẩm ingest lại khi người dùng duyệt và khi thêm file.
  Nếu không lưu layout, các bảng tạo từ layout sẽ mất ở lần build đó.
- **Id họ là mã băm của hình dạng header**, không phải `f1, f2`. Khi thêm file, thứ tự các họ có
  thể đổi, nhưng hình dạng thì không.
- **Model mạnh cho bước đọc bố cục.** Bước này chạy mỗi upload một lần, nên dùng `gpt-oss-120b`
  chỉ tốn khoảng $0,002 đến $0,01 mỗi shop. Câu hỏi vẫn dùng model nhỏ. SpreadsheetAgent cũng
  thấy model mạnh ở bước đọc cấu trúc nâng điểm cả hệ thống.

## Sau held-out: lỗi nằm ở code, không phải ở model

Hai lỗi held-out (bảng chấm công Vào/Ra và ô lịch) được tái tạo trên một **shop tổng hợp**:
dữ liệu tự tạo, có seed, nên commit được. Nhờ vậy sửa được mà không cần mở lại held-out. Khi lưu
lại mọi phương án model đề xuất (`candidates.json`), ta thấy:

- **Model đã đề xuất đúng `pair_rows` ba lần.** Code loại cả ba vì `check_layout` tách tên cột
  `In/Out` theo dấu `/`, rồi báo "không có cột này".
- **Lời khuyên của code đi sai hướng.** Sau khi ghép cặp, kiểm tra hình dạng khuyên dùng
  `split_groups` cho các cột `1 / In, 1 / Out…`, trong khi đúng ra là `unpivot` các ngày.
- **Chẩn đoán ban đầu của tôi cũng sai.** Tôi đoán bảng bị cắt vì "các khối rộng khác nhau".
  Thực ra các khối rộng bằng nhau, và bảng bị cắt vì một dòng toàn giờ (`07:10`) bị coi là header.

Bài học: trước khi đổ lỗi cho model, hãy lưu lại mọi phương án nó đề xuất và mọi phản hồi code đã
gửi về, rồi kiểm tra chẩn đoán bằng số liệu thật. Sau khi sửa code, model đọc đúng 3/3 target của
shop tổng hợp, còn dev vẫn giữ 48/49.
