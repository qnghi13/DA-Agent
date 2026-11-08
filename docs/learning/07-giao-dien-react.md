# 07. Giao diện React: upload, hỏi đáp trực tiếp, biểu đồ và trace

## Khái niệm

**UI chỉ là một client của API.** Mọi thứ UI làm đều đi qua `/v1`, giống hệt `curl` trong README.
UI không có quyền gì đặc biệt, nên các test cô lập workspace ở backend cũng bảo vệ luôn UI. Khi phát
triển, Vite proxy `/v1` sang API ở cổng 8000: trình duyệt thấy một origin duy nhất, nên không cần
bật CORS (bật CORS là mở thêm một cửa cho trang lạ gọi API).

```
trình duyệt ──/v1──> Vite (5173) ──proxy──> API (8000) ──> Postgres <── worker
     └─ Vega vẽ biểu đồ tại chỗ, báo cáo HTML trong iframe sandbox
```

**Luồng demo.** Dán key của workspace → upload file kèm mô tả → LLM đề xuất `DatasetSpec` → người dùng
sửa cột hoặc JSON rồi duyệt → hỏi câu hỏi → xem tiến trình (plan, từng tool với SQL và vài dòng kết
quả, verify) → câu trả lời, biểu đồ, báo cáo → mở trace trong Phoenix nếu là người vận hành.

**Đọc SSE bằng `fetch`, không bằng `EventSource`.** `EventSource` không gửi được header
`Authorization`. Cách vòng là đặt key vào query string, nhưng URL thì bị ghi vào log của proxy và lịch
sử trình duyệt. Vì vậy `sse.ts` tự đọc stream bằng `fetch`, tự tách sự kiện (`SseParser`), và khi mất
kết nối thì nối lại với `Last-Event-ID`. Server lưu sự kiện trong bảng, nên nối lại không mất gì.

**Trạng thái của một run là một reducer.** Mỗi sự kiện SSE đi qua `reduceTrail(state, event)`, một hàm
thuần không có React bên trong. Nhờ vậy test được bằng vitest với danh sách sự kiện giả, không cần
trình duyệt. Sự kiện có thể lặp (at-least-once, xem note 06), nên reducer phải chịu được sự kiện trùng.

**Lịch sử ở server, không ở trình duyệt.** Ban đầu lịch sử câu hỏi nằm trong `localStorage`: đổi máy
là mất, và hai tab không thấy nhau. `GET /v1/runs?dataset_id=` (ADR 0023) trả danh sách theo trang,
mới nhất trước, dùng `next_before` làm con trỏ.

## Vì sao chọn như vậy

| Quyết định | Lý do chính | Nơi |
|---|---|---|
| Vite + React, không framework SSR | API đã có; UI chỉ cần là file tĩnh, phục vụ được từ bất kỳ đâu | plan §1 |
| Key trong `sessionStorage` | Đóng tab là mất key; không có cookie nên không có CSRF | `App.tsx` |
| Vega tải lười (`lazy`) | Vega lớn; chỉ tải khi có biểu đồ trên màn hình | `RunCard.tsx` |
| `ast: true` + `vega-interpreter` | Vega mặc định dịch biểu thức bằng `new Function`; interpreter không cần `eval`, nên CSP chặt vẫn chạy | `Chart.tsx` |
| Báo cáo trong `iframe sandbox="allow-scripts"` | Không có `allow-same-origin`: script trong báo cáo không đọc được key hay gọi API | `ReportViewer.tsx` |
| Bảng màu kiểm bằng script | Màu cho người mù màu tính được, không đoán bằng mắt; bỏ cặp đỏ/xanh lá | `Chart.tsx`, skill `dataviz` |
| Ghim đúng phiên bản, `npm audit` | Vega 5 có advisory mức cao nên lên Vega 6, cả trong báo cáo HTML (có SRI) | ADR 0016 |
| Link trace chỉ cho người vận hành | Phoenix không tách workspace; ai mở được nó là thấy prompt của mọi workspace | [ADR 0023](../adr/0023-trace-links-and-run-list.md) |

**Kiểm tra khi không có trình duyệt.** Môi trường làm việc của Claude không có trình duyệt, nên kiểm tra
theo từng lớp: vitest cho parser SSE và reducer; so kiểu trong `api.ts` với OpenAPI của backend; gọi
qua proxy của Vite; vẽ các spec biểu đồ bằng Vega 6 trong Node ra SVG. Bước cuối, bấm thử toàn luồng,
do người dùng làm trên trình duyệt thật.

## Phương án đã loại

- **`EventSource` với key trong query string:** key lộ trong log và lịch sử trình duyệt.
- **WebSocket:** luồng một chiều, và SSE đã có cơ chế nối lại.
- **Key trong `localStorage`:** sống mãi trên máy dùng chung; một lỗi XSS đọc được nó lâu dài.
- **Chèn báo cáo HTML thẳng vào trang:** script của báo cáo chạy cùng origin với UI và đọc được key.
- **Tự vẽ bảng trace trong UI từ ledger chi phí:** an toàn giữa các workspace, nhưng làm lại những gì
  Phoenix đã có, cho một màn hình chỉ người vận hành dùng.
- **Langfuse tự host:** cần Postgres, ClickHouse, Redis, blob storage, khoảng 13 GiB RAM; máy có 7,6 GB.
- **LangSmith:** mã đóng, tự host chỉ có ở gói Enterprise, prompt chứa dữ liệu của người dùng đi ra
  bên thứ ba. Code chỉ phát OpenTelemetry, nên đổi backend trace chỉ là đổi cấu hình.

## Câu hỏi phỏng vấn

**Stream tiến trình của agent ra UI thế nào, và mất mạng thì sao?**
Worker ghi từng bước vào bảng sự kiện; API gửi bằng SSE với id của bảng. Client đọc bằng `fetch` để
gửi được header auth, nối lại với `Last-Event-ID` và chỉ nhận phần thiếu. Sự kiện có thể lặp nên phía
client gộp theo id.

**Vì sao không dùng `EventSource`?**
Nó không cho đặt header. Đặt token vào URL thì token rơi vào log. Đọc stream bằng `fetch` tốn vài chục
dòng code nhưng giữ token trong header.

**Hiển thị HTML do hệ thống sinh ra một cách an toàn thế nào?**
Đặt trong `iframe` có `sandbox` mà không có `allow-same-origin`: trang con thành một origin riêng, không
đọc được storage hay cookie của trang cha. Thư viện ngoài tải từ CDN có SRI và CSP giới hạn nguồn.

**Observability cho hệ thống agent dùng gì?**
Trace theo chuẩn OpenTelemetry (quy ước GenAI hoặc OpenInference), gửi qua OTLP. Backend tùy chọn:
Phoenix, Langfuse, LangSmith, Datadog. Tách việc phát trace khỏi nơi lưu giúp đổi công cụ mà không sửa
code. Ở đây mỗi lần chạy job là một trace, id của job nằm trong thuộc tính span để tìm lại.

**Ai được xem trace?**
Người vận hành, không phải người dùng cuối. Trace chứa prompt và giá trị ô dữ liệu; công cụ trace thường
không tách theo khách hàng. Vì vậy link trace chỉ bật bằng cấu hình phía server.

**Test UI ra sao khi phần lớn logic nằm trong component?**
Kéo logic ra hàm thuần (parser SSE, reducer, định dạng câu trả lời) và test chúng bằng vitest. Component
chỉ còn nối dữ liệu với giao diện, phần đó kiểm bằng tay hoặc test đầu cuối với trình duyệt.
