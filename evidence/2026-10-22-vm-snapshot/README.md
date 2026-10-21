# VM snapshot: 24 gold controls, matched cost comparison

**Snapshot đã nạp sẵn image giảm 82,26% chi phí mỗi lượt chấm trong pilot này.**
Cả 24 gold patch resolved; toàn bộ 24 official report JSON khớp baseline.
Không chạy full 500 và không đổi backend web production.

Đo ngày **22/10/2026** trên `phamvanvuhoan`. Snapshot preparation chạy
02:20:50–02:34:02, scoring chạy 02:34:25–02:37:17 giờ Việt Nam. Hai lần
đọc billing cuối lúc 02:39:09 và 02:40:38 cho cùng số tiền.

| Chỉ tiêu | VM + OCI Volume baseline | VM từ snapshot đã nạp image |
|---|---:|---:|
| Gold resolved / official reports | 24/24 | 24/24 |
| Report JSON khớp baseline | — | 24/24 |
| Chi phí compute/egress mỗi lượt | $0.98970092 | **$0.17555287** |
| Wall time mỗi lượt chấm | 944.6862960755825 s | **171.66414378210902 s** |
| Tài nguyên VM chấm | 16 cores / 64 GiB | 16 cores / 64 GiB |
| Batch / workers / test timeout | 3 × 8 / 8 / 600 s | 3 × 8 / 8 / 600 s |
| Image lấy từ Volume trong lượt chấm | 24 | 0 |
| Image đã có trong Docker khi khởi động | 0 | 24 |

Chi phí mỗi lượt giảm **$0.81414805, tương đương 82,26%**; wall time
giảm khoảng 81,83%, tức nhanh khoảng 5,50 lần trong phép đo này.

## Chi phí tạo snapshot một lần

| Thành phần | Compute/egress |
|---|---:|
| Canary hai VM kiểm tra snapshot filesystem | $0.00026427 |
| Nạp 24 image, dừng Docker sạch và chụp snapshot | $0.10414732 |
| Tổng setup một lần | **$0.10441159** |
| Setup + lần chấm snapshot đầu tiên | **$0.27996446** |

Tính cả setup và canary, lần dùng đầu tiên vẫn rẻ hơn baseline **71,71%**.
Điểm hòa vốn theo chi phí đã đo là một lượt chấm. Setup mất
792.3195255920291 s; cộng canary và scoring thì lần dùng đầu khoảng
16 phút 10 giây, nên lợi thế latency 5,50 lần chỉ áp dụng khi snapshot đã có.

Đây là số provider meter **trước credits**, chưa phải hóa đơn cuối. Không
cộng lại khoản nạp 500 image trước đây hoặc storage/GCP coordinator dùng
chung. Các snapshot billing được giữ để thể hiện độ trễ: lần đọc đầu ghi
chi phí scoring thấp hơn, rồi hai lần đọc cuối ổn định ở số trên.

## Điều đã được giữ cố định và kiểm chứng

- Dùng đúng 24 instance, thứ tự batch và gold patches của
  [pilot baseline](../2026-10-22-cost-pilot/README.md); checksum dataset
  và predictions bị khóa cứng trong coordinator.
- SWE-bench 4.1.0 / Modal SDK 1.6.2, cùng runtime image
  `im-xE1XGZwwpKZrzcYW0wqjEI`; không rebuild dependency.
- Bốn source evaluator được đọc lại từ VM đang chạy và khớp byte với
  baseline: `core.py`, `remote_worker.py`, `image_cache.py`, `registry_cache.py`.
  Không sửa test, parser hay grading.
- VM tạo snapshot chỉ dùng 2 cores / 8 GiB. Nó nạp đúng 24 image ID của
  baseline từ cache, không có predictions và không tạo test container.
  Docker được shutdown sạch, filesystem được sync trước khi chụp.
- Nạp image mất 748.2058955790001 s, chụp snapshot mất
  35.64839135482907 s. Root filesystem dùng khoảng 45,10 GiB; đây không
  phải số byte storage mà provider tính phí cho snapshot.
- VM scoring mới không gắn Volume, không nhận registry secret. Trước khi
  chấm, kiểm tra đủ 24 image ID và không có container cũ. Thiếu hoặc khác
  image thì fail, không có fallback pull hoặc `docker load`.
- Source image của cả 24 bài là `local`, cache thêm 0 byte. Bước kiểm tra
  image trong evaluator tổng cộng 0.3456105400000169 s; Docker ready sau
  12.668885851278901 s. Snapshot vẫn có I/O khi khởi động/truy cập file;
  toàn bộ thời gian đó nằm trong wall time và billing của lượt chấm.
- 24/24 gold resolved, không thiếu report. So sánh toàn bộ JSON đã parse,
  không chỉ số resolved. 70 test offline Windows và 12 test snapshot trên
  GCP pass. Source helper thực thi khớp các Git checkpoint đã ghi.
- Cả canary, preparation và scoring đều kết thúc; SDK đọc lại thấy không
  còn Sandbox thuộc ba app của phép thử.

## Snapshot và phạm vi sử dụng

Snapshot ID: `im-01M5FZ9JC79WJQHCDCZVGTP7RS`, TTL **30 ngày**. Đây là
filesystem snapshot của VM sạch, giữ Docker image store `/var/lib/docker`.
Mounted Volume không nằm trong snapshot. Snapshot chỉ chứa nhóm 24 image
đã thử, chưa có snapshot cho cả 500 và chưa được tích hợp vào web/CLI chính.

Mẫu lấy gần đều theo repo và chỉ dùng gold controls; không đại diện đầy đủ
cho cơ cấu 500 bài hoặc model patch gây timeout. Đây là một phép đo matched
pilot, không phải cam kết mức tiết kiệm trên mọi lần chạy hay full Verified.

## Bằng chứng và tái lập

- [comparison.json](comparison.json): report parity, timing, chi phí và hòa vốn.
- [billing-final.json](billing-final.json): provider rows theo app/resource;
  hai snapshot trước được giữ nguyên.
- [prepare-receipt.json](prepare-receipt.json), [score-receipt.json](score-receipt.json),
  [canary.json](canary.json): thời gian, resources, lifetime, image/sandbox IDs.
- [preload-manifest.json](preload-manifest.json), [restored-images.json](restored-images.json),
  [image-checks.json](image-checks.json): các image ID trước/sau snapshot.
- [snapshot-reports.json](snapshot-reports.json): 24 report chính thức.
- [prepare-source-provenance.json](prepare-source-provenance.json),
  [score-source-provenance.json](score-source-provenance.json): source hashes.
- [cleanup.json](cleanup.json), [verification.json](verification.json),
  `SHA256SUMS.txt`: cleanup, checks và checksum evidence.

Raw archive giữ tại
`runs/snapshot-pilot-20261022-01/snapshot-evidence-final.tar.gz`; snapshot
filesystem hàng chục GiB không tải về laptop. Public evidence chỉ có metadata,
report và log, không có credentials. Hướng dẫn chạy ở
[probes/SNAPSHOT_PILOT.md](../../probes/SNAPSHOT_PILOT.md).
