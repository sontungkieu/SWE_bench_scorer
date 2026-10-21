# SWE-bench Scorer v0.3.1

Optional `--registry-secret` supports existing Docker Hub credentials for cache
prewarm only. Patch evaluation rejects this option. A WSL bootstrap validates
the supplied username/PAT before storing it directly in Modal Secret; failed
authentication does not fall back to anonymous. PAT-only files can reuse just
an existing username header. 48 offline tests pass.

The existing source Secret was found on kieusontung8, but Modal rejected source
allocation because its spend limit was exceeded. Its stored key has not been
validated or migrated. The original credential file returned HTTP 401. The
encrypted migration helper has offline crypto verification only, no live
qualification. Account spend limits were not changed. A newly supplied PAT authenticated and
created `swebench-dockerhub` on phamvanvuhoan. Authenticated prewarm is running;
completion evidence will distinguish image preload from benchmark evaluation.

A finite GCP coordinator resumes committed images within the chosen $2 budget.
HTTP 429 cooldown holds no Modal VM; unexpected errors stop. Each next allocation
requires a terminated prior VM and a fresh budget check. Web exposes safe cache
progress fields. The adopted 1800-second run plus three further runs has a
CPU/RAM upper allocation bound of $0.663696; with $0.11 prior spend and $0.05
reserve, $0.823696. This is a conservative resource reservation, not an invoice.

## Persistent image cache from v0.3.0

Web và CLI tái sử dụng image giữa các job qua Modal Volume v2. Lệnh `cache`
nạp trước một phần hoặc toàn bộ Verified; lần chạy sau bỏ qua manifest đã có.
Tải trực tiếp layer nén từ Registry API, deduplicate theo SHA-256, pin image ID
và platform manifest digest, kiểm tra hash khi khôi phục. Không chạy Docker
daemon hay giải nén ra disk/nén lại khi prewarm. Không đổi official tests.

Patch rỗng bỏ qua image và harness, vẫn tính unresolved trong mẫu số 500.
Giới hạn cache mặc định 256 GiB blob nén; thời gian hữu hạn và billing guard
trước mọi cloud job. Trên GCP, web/prewarm chia sẻ file lock trước khi tạo VM.
Cache hỏng hoặc thiếu dữ liệu trả lỗi; `--require-cached` cấm cache miss.

Kiểm chứng: 33 test offline pass. Nạp image rồi khôi phục trên VM mới bằng
`--require-cached`, cả gold/wrong/empty đúng kỳ vọng. Với một image Django,
OCI prewarm khoảng 26 giây và restore khoảng 28 giây. Đây là kiểm thử hạ tầng
trên một instance; chưa phải hệ số tăng tốc hay chi phí full 500 bài.
Receipt/source hash và trạng thái prewarm lưu trong thư mục evidence v0.3.0.

Bulk prewarm đã lưu bền vững 101/500 image (~17 GiB gồm prototype), dừng vì
Docker Hub HTTP 429 và terminate VM. 399 image chưa nạp; không có auto retry
hoặc VM giữ để chờ quota. Tổng app compute/egress của các thử nghiệm cache và
mock được báo cáo $0.10410261 trước credits; bulk run riêng $0.04905196.
Đây là snapshot v0.3.0; credential và các lượt nối tiếp được ghi trong evidence v0.3.1.

Cấu hình GCP cũ được giữ, tự dùng cache mặc định. Hướng dẫn prewarm, resume,
tắt cache và giới hạn chi phí ở README. Storage allowance dùng chung workspace.

## Web từ v0.2.0

Web riêng tư chạy trên GCP VM hiện có, truy cập qua SSH tunnel. Upload JSON,
JSONL hoặc CSV, validate input, chọn smoke/full, chạy mock, theo dõi lịch sử và
live log, tải report/log gốc. Official Docker harness vẫn chạy trên Modal CPU.

Ba trường bắt buộc: `instance_id`, `model_name_or_path`, `model_patch`.
JSON nhận list hoặc mapping theo ID. CSV giữ nguyên patch nhiều dòng khi cell
được quote đúng. Cả CLI và web chuẩn hóa input sang JSONL trước khi chấm.

Kiểm chứng: 19/19 test trên Windows và GCP; mock tạo qua web pass 3/3 control;
CSV upload qua web chấm thật một gold patch, resolved, không thiếu report.
Hai run đều hoàn tất và terminate sandbox. Đây là kiểm thử hạ tầng trên cùng
một instance, không phải điểm VAI hoặc qualification full 500 bài.

UI được phục vụ qua HTTP 200 và API đã kiểm tra end-to-end. Browser automation
không khởi động được do lỗi kernel assets của môi trường Codex; visual/browser
QA chưa được xác nhận. Đây không phải kết quả kiểm tra giao diện trực quan.

Queue SQLite bền vững, một job chạy mỗi lần, billing guard trước launch,
credential Modal scoped nằm ngoài source, loopback listener sau SSH. Đóng tab
không mất job. Job bị gián đoạn không tự chạy lại để tránh chi phí trùng lặp.

Xem `deploy/README.md` để mở SSH tunnel và vận hành service. Full 500 cần chọn
riêng, xác nhận và đủ ngân sách; chưa chạy trong lần setup này. Dự trù guard
không phải hard billing cap. Không có credential trong repo/release asset.
