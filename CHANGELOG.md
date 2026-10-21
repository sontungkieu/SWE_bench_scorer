# Changelog

## 0.3.1 - 2026-10-21

- Optional Docker Hub Basic auth via a named Modal Secret for cache-only VMs;
  evaluation modes reject registry secrets. Failed auth never falls back to anonymous.
- Validate an existing WSL username/PAT file before creating a destination Secret;
  no PAT in arguments, logs, source or extra plaintext files.
- Bounded encrypted migration helper for an existing cross-workspace Secret.
  Live source allocation was blocked by Modal spend limit; no validity claim.
- Support a PAT-only credential file with a separate username header; Docker Hub
  authenticated JWT subjects may be opaque account IDs.
- Finite GCP coordinator resumes clean time boundaries, waits outside Modal on
  HTTP 429, and reserves allocation bounds before each new VM. Web shows cache
  progress through a public-field-only endpoint.
- 48 offline tests cover auth, redirects, secret exclusion, budget/termination
  gates, quota cooldown and finite-work resumptions. New PAT authenticated live;
  all-500 cache completion is recorded separately from any benchmark grading.

## 0.3.0 - 2026-10-21

- Persistent image cache on a Modal v2 Volume: deduplicated OCI compressed blobs,
  SHA-256 verification, pinned image IDs/digests, atomic committed manifests.
- `cache` downloads layers directly from the registry without Docker unpacking
  or recompression; CLI prewarms selected/all 500 images and resumes completed
  entries; storage/time bounds stop work without deleting completed cache.
- Web and CLI share the cache by default; GCP coordinator serializes writers.
- Empty patches skip image loading and tests while remaining unresolved in score.
- Cross-VM cache restore passed all three mock controls; 33 offline tests pass.
  Full 500 image prewarm is separately recorded; no full benchmark eval claimed.

## 0.2.0 - 2026-10-21

- Web upload và chấm qua GCP SSH tunnel: validate, queue, live log, kết quả,
  download artifact, mock controls và lịch sử job bằng SQLite.
- JSON list/ID mapping và CSV (gồm multiline patch/BOM) cùng JSONL; chuẩn hóa
  thành JSONL trước khi gửi official harness.
- GCP chỉ chạy web/Modal client; chấm Docker trên Modal, một worker mỗi lượt.
- Portable billing guard, profile scoped, systemd service và source/state tách biệt.
- Chặn cross-origin POST, job idempotency, không tự retry job trả phí bị gián đoạn.
- 19 test pass trên Windows và GCP; web mock 3/3 control đúng kỳ vọng;
  CSV upload chấm thật một gold patch resolved, artifacts được tải và verify hash.
  Visual/browser QA chưa xác nhận (automation lỗi); full 500 chưa chạy.

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
