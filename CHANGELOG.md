# Changelog

## 0.2.0 - 2026-10-21

- Web upload và chấm qua GCP SSH tunnel: validate, queue, live log, kết quả,
  download artifact, mock controls và lịch sử job bằng SQLite.
- JSON list/ID mapping và CSV (gồm multiline patch/BOM) cùng JSONL; chuẩn hóa
  thành JSONL trước khi gửi official harness.
- GCP chỉ chạy web/Modal client; chấm Docker trên Modal, một worker mỗi lượt.
- Portable billing guard, profile scoped, systemd service và source/state tách biệt.
- Chặn cross-origin POST, job idempotency, không tự retry job trả phí bị gián đoạn.
- Kiểm chứng parser/HTTP/queue/score bằng 19 test offline; qualification web
  cloud được ghi riêng, không coi unit test là kết quả full 500 bài.

## 0.1.1 - 2026-10-21

- Chuẩn hóa text LF bằng .gitattributes, sửa checksum fixture và evidence để
  khớp byte đọc lại từ GitHub trên Windows/Linux.
- Giữ nguyên log/receipt mock v0.1.0 và hành vi grading; không chạy lại cloud
  chỉ để thay version/checksum metadata. 7/7 test offline pass, gồm regression
  test checksum fixture/evidence.

## 0.1.0 - 2026-10-21

- Official SWE-bench 4.1.0 Docker harness chạy trong Modal VM CPU, client 1.6.2.
- Pin dataset Verified test bằng revision, kiểm tra đủ 500 ID duy nhất.
- Lệnh validate offline, mock, smoke và full; không cài dependency/Docker local.
- Mock bài thật django__django-11099 với gold, wrong và empty patch tách biệt.
- Billing guard trước launch, giới hạn thời gian, tự terminate và tải artifact.
- Receipt ghi hash source, runtime/image ID, dataset manifest và dependency freeze.
- Full giữ patch rỗng trong mẫu số 500; không xuất điểm hợp lệ khi thiếu report.
- Full tải/chấm image theo batch và dọn image để giới hạn dung lượng disk.
- Kiểm chứng: 6 test offline; mock Modal có ba control. Chưa chạy full 500 bài,
  chưa đánh giá checkpoint VAI và chưa xác nhận mọi repository/runtime của Verified.
