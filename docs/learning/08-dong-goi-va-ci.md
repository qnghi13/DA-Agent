# 08. Đóng gói: Compose, Runner tách riêng, CI

## Khái niệm

**Ai giữ Docker socket là có quyền root trên máy chủ.** Qua socket có thể chạy
`docker run -v /:/host --privileged ...` và đọc, ghi mọi thứ trên host. Vì vậy ADR 0005 đã quyết từ đầu:
chỉ Runner giữ socket. Nhưng cho đến Ngày 19, worker vẫn tạo `Runner` ngay trong process của nó. Khi
chạy thẳng trên máy dev thì không thấy khác biệt. Đến lúc đóng gói, câu hỏi "container nào mount
`/var/run/docker.sock`" buộc phải trả lời, và câu trả lời đúng là: không phải worker. Worker giữ API key
của model, đọc output của model và xử lý file người dùng upload, tức là nơi dễ bị tấn công nhất.

```
web (nginx) ─> api ─┐
                    ├─ postgres         backend: mạng internal, không có đường ra internet
worker ─HTTP+token─> runner ─socket─> sandbox (--network none)
   └─> Groq (mạng edge)
```

**Runner thành một service nhỏ.** Nó nhận đúng ba loại yêu cầu có kiểu (`query`, `ingest`, `build`),
cần token, không có key của model, không có URL database và không ra được internet. Worker dùng
`RunnerClient` có cùng các method với `Runner`, nên pipeline và graph không phải sửa một dòng nào. Đây
là lợi ích của việc đặt interface (Protocol `SandboxRunner`) ở ranh giới ngay từ đầu.

**Docker-out-of-Docker và đường dẫn.** Runner chạy trong container nhưng ra lệnh cho Docker daemon của
host. `--mount type=bind,source=/data/datasets/x` được daemon hiểu là `/data/...` *trên host*, không phải
trong container Runner. Cách giải quyết là để dữ liệu trên một named volume (`da-agent-data`) và mount
đúng thư mục con vào sandbox bằng `volume-subpath` (Docker 26 trở lên). API, worker và Runner mount
cùng volume ở cùng đường dẫn, nên một `Path` trong kết quả có cùng nghĩa ở cả ba nơi.

**Cùng một uid cho những ai ghi lên volume.** Sandbox ghi file kết quả, worker cần di chuyển và xóa
chúng. Nếu mỗi bên một uid thì worker không xóa được file của sandbox. Vì vậy API, worker và sandbox
cùng chạy với uid 10001 (`DA_AGENT_SANDBOX_USER`). Image tạo sẵn `/data` thuộc 10001, và named volume
copy quyền sở hữu đó trong lần mount đầu tiên.

## Vì sao chọn như vậy

| Quyết định | Lý do chính | Nơi |
|---|---|---|
| Runner là service HTTP riêng | Chỉ nó giữ socket; worker bị chiếm cũng không chọn được mount hay flag | [ADR 0024](../adr/0024-runner-service.md) |
| Token so sánh thời gian hằng số | Một container lạ trong mạng backend không gọi được Runner | `runner/service.py` |
| Mạng `backend` internal | Postgres và Runner không có đường ra internet; worker ra qua mạng `edge` | `compose.yml` |
| Kết quả Runner nằm ngoài staging thì bỏ | Phòng thủ nhiều lớp: client không tin mù quáng đường dẫn trả về | `runner/client.py` |
| nginx ghi đè `X-Forwarded-For` | Client không giả được địa chỉ để né rate limit; API không publish cổng | `web/nginx.conf`, ADR 0022 |
| Image ghim theo digest | Build lại vẫn ra cùng base; đổi base là một thay đổi có chủ đích trong git | `deploy/app.Dockerfile` |
| Action ghim theo SHA của commit | Tag có thể bị dời sang commit khác; SHA thì không | `.github/workflows/ci.yml` |
| CI không gọi model thật | Miễn phí, chạy được trên fork; smoke test chỉ kiểm tra stack lên và trả lời đúng mã | `deploy/smoke.sh` |

**Kiểm tra trên stack thật.** `docker compose up` rồi upload `examples/demo-shop/` qua nginx giống
trình duyệt, duyệt spec, hỏi "Doanh thu tháng 6/2026?". Kết quả 8.714.000 khớp con số script sinh dữ
liệu tính trước, có báo cáo và link trace, tốn $0,001. Sau đó kiểm tra thẳng trong container: worker
không có socket và không có lệnh `docker`, Runner không gọi được ra internet và không có biến môi trường
chứa key, gọi Runner không có token thì nhận 401.

## Phương án đã loại

- **Để worker giữ socket rồi ghi nhận rủi ro:** nhanh nhất nhưng bỏ đúng ranh giới mà threat model dựa
  vào.
- **docker-socket-proxy:** chặn được theo đường dẫn API, nhưng `containers/create` vẫn nhận mount tùy
  ý, nên worker vẫn mount được `/` của host.
- **Hàng đợi tin nhắn giữa worker và Runner:** lời gọi ngắn và đồng bộ; Postgres đã xếp hàng job.
- **Mount thư mục host cùng đường dẫn tuyệt đối ở mọi container:** chạy được, nhưng gắn chặt vào một
  đường dẫn host và hỏng trên Docker Desktop, nơi daemon chạy trong VM.

## Câu hỏi phỏng vấn

**Vì sao mount Docker socket vào container là nguy hiểm?**
Socket là API quản trị của daemon, mà daemon chạy bằng root. Ai gọi được nó là tạo được container
privileged mount `/` của host. Nên chỉ đưa socket cho một service nhỏ, nhận đầu vào có kiểu và tự quyết
mọi tham số của container.

**Chạy code do LLM sinh ra một cách an toàn thế nào?**
Một container cho mỗi yêu cầu: không mạng, filesystem chỉ đọc, user không phải root, bỏ mọi capability,
giới hạn RAM, CPU, số process và thời gian, chỉ mount đúng dữ liệu cần. Service điều phối container tách
khỏi service giữ secret. Runc không phải ranh giới tuyệt đối; muốn mạnh hơn thì dùng gVisor hoặc
microVM như Firecracker.

**Docker-out-of-Docker khác Docker-in-Docker thế nào?**
DooD dùng daemon của host qua socket: nhẹ, nhưng đường dẫn mount là đường dẫn host và có quyền root của
host. DinD chạy một daemon riêng trong container privileged: tách biệt hơn về trạng thái, nhưng cần
`--privileged`, nên cũng không an toàn hơn.

**CI của một hệ thống LLM nên kiểm tra gì khi không muốn tốn tiền?**
Lint, unit test với model giả và phản hồi ghi sẵn, test integration với Postgres và Docker thật, test
bảo mật tấn công vào sandbox, build image và smoke test toàn stack. Eval với model thật chạy tay hoặc
theo lịch, có ngân sách.

**Secret trong Compose để ở đâu?**
Trong `.env` không commit, chỉ truyền đúng biến cho đúng service: Runner không nhận key của model, API
không nhận key của model. `${VAR:?message}` làm Compose dừng với thông báo rõ ràng khi thiếu secret.

**CD ở dự án không có server thì là gì?**
Là phát hành thứ người khác chạy được ngay. Đẩy tag `v0.1.0` thì workflow release chạy lại toàn bộ
CI, build bốn image và đẩy lên GHCR, rồi kéo đúng các image vừa đẩy về chạy smoke test
([ADR 0028](../adr/0028-release-images-to-ghcr.md)). Kiểm tra image "như đã phát hành" chứ không
phải "như đã build", vì lỗi tên image hoặc thiếu file chỉ lộ ra ở bước kéo về.

**Giảm rủi ro chuỗi cung ứng trong pipeline phát hành thế nào?**
Ít thành phần bên thứ ba chạm vào token nhất có thể: ở đây chỉ dùng Docker CLI có sẵn trên runner,
token `GITHUB_TOKEN` chỉ có quyền `packages: write` trong đúng job publish, action còn lại được pin
theo SHA. Mỗi image kèm provenance (SLSA) và SBOM để biết nó được build từ commit nào, có gói gì.
Bước tiếp theo nếu cần là ký image bằng cosign và kiểm tra chữ ký khi kéo về.
