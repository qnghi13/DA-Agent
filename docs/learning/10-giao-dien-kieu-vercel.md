# 10. Thiết kế lại giao diện: chat làm trung tâm, bằng chứng một cú bấm

## Khái niệm

**Giao diện cho một agent khác giao diện cho một form.** Với agent, người dùng cần thấy hai thứ
khác nhau ở hai thời điểm khác nhau:

- **Khi agent đang chạy:** biết nó còn sống và đang làm gì. Nếu màn hình đứng yên 10 giây, người dùng
  nghĩ app bị treo. Vì vậy tin nhắn hiện tiến trình trực tiếp qua SSE: "Querying the data · 2
  queries", từng bước có dấu tích, và đồng hồ đếm giây.
- **Khi agent đã xong:** đọc đáp án, rồi tin hoặc kiểm tra. Lúc này các bước là nhiễu, nên chúng
  co lại thành một dòng ("✓ Verified · 1 query"). Bằng chứng (kế hoạch, SQL, các dòng dữ liệu
  model đã thấy, báo cáo, trace) nằm trong panel bên phải, mở bằng "Show work".

Hai quy tắc giữ cho việc chuyển trạng thái không gây khó chịu: **không có nội dung nào biến mất**
(tiến trình co lại thành một dòng tóm tắt chứ không bị xoá), và **panel không bao giờ tự mở** (tự
mở sẽ xô lệch bố cục khi người dùng đang đọc).

**Design token.** Màu được định nghĩa bằng biến CSS trong `web/src/index.css`, một bộ cho chế độ
sáng và một bộ dưới class `.dark`. Component chỉ dùng tên vai trò (`bg-card`, `text-muted-foreground`,
`text-success`), không dùng mã màu. Đổi chế độ sáng/tối chỉ là bật tắt một class trên `<html>`.
Màu chỉ dùng để chỉ trạng thái: xanh lá là verified/ready, xanh dương là đang chạy, hổ phách là
cần duyệt, đỏ là lỗi. Mỗi màu luôn đi kèm chữ hoặc icon, không bao giờ để màu đứng một mình.

**Bảng màu biểu đồ được tính, không chọn bằng mắt.** Script `validate_palette.js` của skill
`dataviz` kiểm tra độ sáng, độ bão hoà, khoảng cách giữa các màu kề nhau khi mô phỏng mù màu
(ΔE ≥ 8), và độ tương phản với nền thẻ của từng chế độ. Ba màu ở chế độ sáng có tương phản dưới
3:1. Vì vậy biểu đồ phải có cách đọc khác thay thế: tooltip, tab Data, và bảng số liệu bên cạnh
biểu đồ ở `/ops`.

## Vì sao chọn như vậy

- **Tailwind + shadcn/ui** ([ADR 0027](../adr/0027-tailwind-and-shadcn-ui.md)). Phần khó nhất của
  giao diện này là panel trượt, dialog và menu: giữ focus bên trong, đóng bằng Esc, trả focus về
  nút đã mở, phủ toàn màn hình trên điện thoại. Radix (bên dưới shadcn) đã làm đúng những việc
  đó. shadcn chép mã nguồn component vào repo, nên vẫn sửa được như code của mình.
- **Panel nằm cạnh khung chat trên desktop, `Sheet` trên điện thoại.** Ở màn hình rộng, panel là
  một `<aside>` không chặn tương tác: khung chat hẹp lại nhưng vẫn cuộn được, vẫn hỏi tiếp được.
  Ở màn hình hẹp không đủ chỗ cho hai cột, nên dùng dialog phủ toàn màn hình. Chọn giữa hai cách
  bằng `useMediaQuery("(min-width: 1024px)")`.
- **Không đổi API.** Dòng tóm tắt chỉ dùng những gì API đã trả về. "Verified" khi
  `stop_reason == "answered"`, vì graph chỉ trả lời sau khi verify đã pass. Thời gian chạy và chi
  phí không có trong run trả về cho member, nên không hiện; đồng hồ trong lúc chạy do client tự
  đếm.
- **Thay từng màn hình, không viết lại một lần.** CSS cũ được bọc trong `.legacy-main` bằng CSS
  nesting để không tràn sang component mới, rồi bị xoá khi màn hình cuối cùng đã chuyển xong. App
  dùng được sau mỗi bước, và mỗi bước đều được chụp màn hình ở 1440 và 390 px, sáng và tối.
- **Logic thuần có test trước:** `theme.ts` (chọn chế độ, storage bị chặn), `status.ts` (trạng thái
  → nhãn, màu, bước), `run.ts` (`answerSummary`, `activity`).

## Phương án đã loại

- **CSS tự viết với token kiểu Vercel:** không thêm thư viện, nhưng phải tự làm focus trap, Esc và
  bản phủ toàn màn hình trên mobile, tức là nhiều công hơn mà kết quả kém hơn.
- **Radix + CSS tự viết:** có accessibility chuẩn, nhưng toàn bộ style vẫn phải tự làm.
- **Bố cục dashboard có tabs (A) và workbench chia đôi (C):** A giống Vercel nhất nhưng hỏi liên tục
  không tự nhiên; C làm nổi bật bằng chứng nhưng chật trên điện thoại. Bố cục B (chat ở giữa) kết
  hợp với panel bằng chứng của C giữ được cả hai điểm mạnh.
- **Hiện tất cả mọi thứ trong tin nhắn (mở rộng tại chỗ):** đơn giản, nhưng khi hỏi nhiều câu thì
  khung chat dài và khó đọc.
- **Gói `cn` mà shadcn CLI tự thêm vào:** không được pin phiên bản và trùng với `clsx` +
  `tailwind-merge` đã chọn, nên các import được đổi sang `@/lib/utils`.

## Câu hỏi phỏng vấn

**Bạn hiển thị tiến trình của một agent chạy lâu như thế nào?**
Server đẩy sự kiện từng bước qua SSE, có id để kết nối lại mà không lặp sự kiện. Client gộp các sự
kiện thành một "trail" bằng reducer thuần (đã có test). Trong lúc chạy hiện bước hiện tại và đồng
hồ; khi xong, trail co lại thành một dòng và chuyển vào panel.

**Làm sao để người dùng tin câu trả lời của LLM?**
Không yêu cầu họ tin. Mỗi đáp án có nhãn "Verified", chỉ hiện khi các kiểm tra bằng code đã pass.
Cách truy nguồn chỉ cách một cú bấm: SQL đã chạy, các dòng dữ liệu model đã thấy, và báo cáo HTML
trong đó mỗi con số link tới ô dữ liệu sinh ra nó.

**Dark mode làm sao cho đúng?**
Token theo vai trò, hai bộ giá trị, một class trên `<html>`. Mặc định theo hệ thống, người dùng có
thể chọn khác, và lựa chọn được lưu trong `localStorage` (bỏ qua nếu storage bị chặn). Biểu đồ
không tự đảo màu: chúng có bộ màu tối riêng, được kiểm định trên nền tối.

**Thêm thư viện UI có rủi ro gì?**
Chuỗi cung ứng và kích thước bundle. Mỗi gói đều được pin chính xác, là bản đã phát hành ít nhất
hai tuần, và CI chạy `npm audit`. Bundle chính tăng từ 83 lên 140 kB (gzip); với một công cụ nội
bộ thì chấp nhận được, còn Vega vẫn chỉ tải khi có biểu đồ.
