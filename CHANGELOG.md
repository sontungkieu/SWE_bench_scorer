# Changelog

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
