# SWE-bench Scorer v0.2.0

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
