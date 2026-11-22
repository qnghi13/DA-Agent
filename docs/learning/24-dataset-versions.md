# 24. Thêm file vào dataset: phiên bản dữ liệu

Sau nhóm **harness** (note [23](23-slo-alerts.md)), đây là một tính năng sản phẩm, nhưng bài học
chính vẫn là độ tin cậy: **dữ liệu đổi giữa chừng không được làm câu trả lời cũ sai lặng lẽ**.

## Bài toán

Người dùng đang chat thì phát hiện thiếu dữ liệu (đơn hàng tháng sau, danh sách khách hàng). Upload
mới thì mất hội thoại, ghi chú, metric, và tốn thêm một lần model đề xuất spec. Ba cách làm:

| Cách | Ý tưởng | Vấn đề |
|---|---|---|
| A (chọn) | **Version** mới trong cùng dataset | cần bảng version, nhưng giữ được id, chat, ghi chú |
| B | Dataset mới, liên kết với cũ | ghi chú và metric gắn với `dataset_id` nên phải sao chép |
| C | Ghi thêm dòng thẳng vào bảng cũ | run đang chạy đọc dữ liệu đổi giữa chừng; câu trả lời cũ đổi khi chạy lại |

Giống migration trong database: **không sửa tại chỗ, tạo phiên bản mới rồi chuyển con trỏ**. Chỉ
`apply` đổi `current_version`, trong một transaction; chat vẫn chạy trên bản cũ trong lúc kiểm tra.

## Code quyết định, model chỉ đề xuất

Mỗi bảng trong file mới được so với bản hiện tại theo **header**: không đổi, **lớn thêm** (cùng
header) hoặc **mới**. Rồi:

- Cùng dạng, không trùng, chất lượng ổn: áp dụng luôn, **không gọi model**.
- **Trùng dòng** (theo khóa, dòng giống hệt, hoặc chỉ trùng khoảng ngày): hỏi người dùng: thay file
  cũ, bỏ dòng trùng, hoặc giữ cả hai. Không bao giờ tự cộng hai lần.
- Bảng mới, hoặc dòng mới parse kém: model đề xuất, **chỉ cho bảng mới** và sửa **chỉ cột hỏng**,
  hiển thị trước/sau để người dùng duyệt. Spec cũ (kể cả chỉnh sửa của người dùng) giữ nguyên.

Hai quy tắc đáng nhớ, đều đến từ review:
1. Chỉ **code** được ghi `drop_overlap`. Nếu để model viết, một đề xuất sai có thể xóa dòng.
2. `drop_overlap` chỉ có khi trùng theo khóa hoặc dòng giống hệt. Trùng chỉ theo ngày thì không
   biết dòng nào là dòng trùng, nên chỉ cho "thay" hoặc "giữ cả hai". Đưa ra lựa chọn không làm
   được gì là lỗi, vì người dùng tin là đã xử lý.

## Câu trả lời cũ

Mỗi run ghi **version nó đã đọc**; export, query, ghi nhớ đọc đúng version đó. Trong prompt, lượt cũ
được gắn nhãn "dữ liệu cũ hơn". Chi tiết dễ sai: nhãn so với version **của chính run**, không phải
version hiện tại của dataset. Nếu so với version hiện tại, một run được resume sau khi có version
mới sẽ dựng prompt khác lần đầu, phá tính tất định của checkpoint ([note 03](03-graph-va-checkpoint.md)).

## An toàn khi chạy lại và hỏng giữa chừng

- Worker đọc lại version dưới khóa; nếu đã bị hủy hoặc sai trạng thái thì **không làm gì**.
- `apply` kiểm tra trạng thái mong đợi; một lần chạy muộn không áp dụng bản đã hủy, không áp dụng hai lần.
- Lỗi thì version thành `failed`, bản hiện tại vẫn phục vụ.
- API **kiểm tra trước** khi xếp job (422): lựa chọn đúng bảng, sửa spec chỉ cho bảng mới, repair
  nằm trong tập đã đề xuất. Nếu để worker phát hiện thì người dùng chờ lâu rồi mới thấy lỗi.

## Còn thiếu (nói thật)

- Runner sập lúc kiểm tra hiện làm version `failed` và xóa file đã upload; phải sửa thành retry
  trước khi merge.
- Chưa chạy với model thật hay trong trình duyệt.

## Tự kiểm tra

1. Vì sao cách C (ghi thẳng vào bảng cũ) làm hỏng "câu trả lời cũ không được đổi khi chạy lại"?
2. Vì sao nhãn "dữ liệu cũ" lấy version của run chứ không phải của dataset?
3. Một bảng chỉ trùng theo khoảng ngày: vì sao không cho chọn "bỏ dòng trùng"?
