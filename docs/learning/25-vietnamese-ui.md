# 25. Giao diện tiếng Việt: i18n bằng từ điển có kiểu

Sau tính năng thêm file (note [24](24-dataset-versions.md)), đây là việc **quốc tế hoá** (i18n):
cho giao diện nói thêm tiếng Việt. Agent vốn đã trả lời theo ngôn ngữ câu hỏi và sửa được chữ viết
tắt, Telex (`agent/vietnamese.py`); phần còn tiếng Anh là mọi thứ **xung quanh** câu trả lời.

## i18n là gì, và vì sao không chỉ "dịch chuỗi"

Dịch một giao diện có ba phần khó hơn việc thay chữ:

1. **Trật tự từ.** "number of different Mã KH" thành "số Mã KH khác nhau": chữ "khác nhau" đứng
   sau tên cột. Nếu code ghép câu bằng `"number of " + distinct + column`, không dịch được. Vì vậy
   mục nào có dữ liệu chèn vào là **một hàm** trong từ điển, mỗi ngôn ngữ tự sắp xếp câu của mình.
2. **Định dạng số và ngày.** `1,234.5` (en-US) và `1.234,5` (vi-VN): dấu chấm và phẩy đảo ngược.
   Dùng `Intl.NumberFormat(m.locale)`, không tự viết.
3. **Chữ từ server.** Lỗi API là câu tiếng Anh. Phải có mã ổn định thì web mới dịch được.

## Ba cách đã cân nhắc

| Cách | Ý tưởng | Vì sao chọn hoặc bỏ |
|---|---|---|
| A (chọn) | Từ điển `en.ts`, `vi.ts` tự viết, có kiểu TypeScript | không thêm thư viện; thiếu key là `tsc` báo lỗi |
| B | `react-i18next` | số nhiều, tải lười, công cụ cho người dịch: hai ngôn ngữ không cần; tiếng Việt không có số nhiều |
| C | Server dịch theo `Accept-Language` | phần lớn chữ nằm ở web; phải sửa nhiều endpoint |

Mấu chốt của A: `export type Messages = typeof en`, rồi `export const vi: Messages = {...}`. Thiếu
hoặc thừa một key, hay để chuỗi chỗ cần hàm, đều là lỗi kiểu, nên **không thể build** một giao
diện thiếu bản dịch.

## Mã lỗi thay vì dịch ở server

API giữ `detail` tiếng Anh (client cũ vẫn chạy) và thêm `code`, `params` cho những lỗi người dùng
hay gặp: `upload.too_many_files {max: 20}`, `limit.budget {limit: 2.0}`... Web tra mã trong từ điển.
Mã lạ, hoặc thiếu param mà câu cần, thì **hiện nguyên câu tiếng Anh của server**: một `Proxy` ném
lỗi khi hàm dịch đọc param không có, nên không bao giờ hiện chữ "undefined".

## Đổi ngôn ngữ không cần tải lại trang

Chữ được tính **lúc render** từ mã (trạng thái `running`, lý do dừng `cancelled`...), không lưu
sẵn chữ đã dịch vào state. Đổi ngôn ngữ thì context đổi, mọi thứ vẽ lại. Ví dụ thông báo đăng xuất
được lưu là `{ kind: "session-ended" }`, không phải câu văn.

## Giữ màn hình sạch bằng test

`literals.test.ts` đọc mọi file `.tsx` (qua `import.meta.glob` của Vite, không cần `node:fs`) và
báo lỗi khi thấy chữ tiếng Anh viết hoa trong JSX, thuộc tính `aria-label`/`placeholder`, hay chuỗi.
Khi làm, danh sách `PENDING` các file chưa dịch nhỏ dần đến rỗng: mỗi bước có một test đỏ trước.
Test này là heuristic: chữ viết thường lọt qua, nên vẫn phải đọc từng file.

## Những gì vẫn là tiếng Anh

Dữ liệu và tên cột (không bao giờ dịch), câu trả lời của agent (theo ngôn ngữ câu hỏi), báo cáo
HTML (làm sau), trang vận hành cho operator, và các lỗi server chưa có mã. Quyết định: ADR 0046.
