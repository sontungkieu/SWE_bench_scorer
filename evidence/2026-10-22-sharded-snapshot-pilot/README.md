# Kiểm chứng đúng 24 bài trên catalog 11 snapshot mới

**24/24 gold controls resolved; toàn bộ 24 parsed official report JSON khớp
baseline VM + OCI Volume và khớp snapshot pilot 24-image cũ.**
Lượt chấm mới ghi nhận **$0.50332977**, wall **515.2492166236043 giây**
(8 phút 35.25 giây). Không chấm full 500 và không đổi backend production.

## Kết quả so sánh

| Đường chạy đúng cùng 24 gold patch | Official report/resolved | Compute + egress trước credits | Wall |
|---|---:|---:|---:|
| VM nạp OCI image từ Volume, baseline cũ | 24/24 | $0.98970092 | 944.6862960755825 s |
| Một snapshot chứa riêng 24 image, pilot cũ | 24/24 | $0.17555287 | 171.66414378210902 s |
| **Catalog 11 snapshot mới, dùng 8 snapshot có bài trong mẫu** | **24/24** | **$0.50332977** | **515.2492166236043 s** |

So với baseline OCI, lượt mới giảm **49.1432452% chi phí**, wall nhanh
**1.8334551 lần**. So với snapshot riêng 24-image cũ, lượt mới tốn khoảng
**2.8671122 lần tiền** (+186.711217%) và mất khoảng **3.0014959 lần thời gian**.
Vì vậy mức giảm 82.26% ở pilot snapshot cũ **không phải mức đã đo cho cách chia
shard và lịch chạy mới này**.

Đây là so sánh các lần đo thực tế, không phải một thí nghiệm giữ nguyên mọi
yếu tố: cách chia batch, số VM, số lần khởi động và môi trường cache/host theo
thời gian có khác. Report parity là bằng chứng về kết quả 24 controls; nó
không làm cho so sánh performance trở thành một ablation chỉ đổi một biến.

## Phạm vi và cấu hình thực sự chạy

- Profile `phamvanvuhoan`, Modal SDK 1.6.2, SWE-bench 4.1.0, runtime `vm`.
- [App](https://modal.com/apps/phamvanvuhoan/main/ap-kKVKLYIKmGx8twFKWN4IJd):
  `ap-kKVKLYIKmGx8twFKWN4IJd`.
- Bắt đầu **07:02:27**, kết thúc **07:11:02 ngày 22/10/2026, UTC+7**.
- Mỗi VM: **16 physical CPU cores / 64 GiB**, request = limit;
  `max_workers=8`, test timeout **600 giây**, giống cấu hình pilot trước.
- Tối đa **một VM chạy tại mỗi thời điểm**; 8 VM độc lập, mỗi VM khôi phục đúng
  snapshot đã tạo trước đó. Không tạo snapshot mới trong lượt eval.
- Tổng deadline coordinator 1800 giây; lifetime mỗi VM tối đa 780 giây;
  không bắt đầu nhóm mới nếu không còn đủ dự phòng cho timeout test cũ.
- Dự trù guard **$2**, CPU/RAM envelope $1.90368 trong luồng tuần tự bình thường;
  guard đã trả OK trước khi cấp VM. Đây không phải provider hard cap/invoice.
- Không gắn cache Volume, không truyền registry credentials, thiếu/khác image
  thì fail; không có fallback `docker load` hay registry pull.

24 bài nằm trong **8/11 snapshot**: index **0, 2, 4, 6, 7, 8, 9, 10**. Ba
snapshot 1, 3, 5 không chứa bài nào của mẫu 24 nên không chạy test thêm chỉ để
dùng chúng. Trước đó cả 11 đã được qualification khi build; lượt này không
bổ sung SWE-bench test cho các instance ngoài mẫu đã khóa.

| Shard | Bài chấm | Docker ready (s) | Test + cleanup trong worker (s) | VM wall (s) |
|---|---:|---:|---:|---:|
| 0 | 4 | 23.73 | 106.47 | 134.72 |
| 2 | 2 | 17.29 | 39.97 | 61.34 |
| 4 | 1 | 10.49 | 66.92 | 81.32 |
| 6 | 6 | 17.25 | 50.71 | 70.46 |
| 7 | 3 | 9.28 | 36.61 | 49.89 |
| 8 | 4 | 8.07 | 27.81 | 40.08 |
| 9 | 3 | 17.61 | 26.73 | 48.86 |
| 10 | 1 | 3.67 | 21.98 | 27.80 |

Số worker tối đa vẫn là 8 nhưng mỗi nhóm mới chỉ có 1–6 bài, thay cho ba batch
8 bài của pilot cũ. Do đó mức song song thực tế và thời gian đường tới hạn
của cả lượt thay đổi. Không trộn các snapshot hoặc nạp thêm image để giả lập
một VM chứa tất cả 24 bài.

## Kiểm chứng logic và kết quả

Git checkpoint thực thi:
`b6191e24c99c175ba46e2debcc1d4c5616946e66`.
Code/source package được kiểm tra hash trên GCP trước launch; 8 preflight unit
test chạy trên host GCP pass. Source/helper trong mỗi VM sau restore được đọc
lại và so hash trước khi chấm: **cả 8/8 VM đều khớp**. Bốn file evaluator gốc
khớp byte với baseline: `core.py`, `remote_worker.py`, `image_cache.py`,
`registry_cache.py`. Hai helper worker cũng được xác nhận trên từng VM.

Input vẫn đúng các byte của pilot gốc:

```text
pilot.json   SHA256 47b545950e6fc204b2d028573298f4ee65a9497c035012bfe9ffe709c91ddbbf
preds.jsonl  SHA256 aa914e991bbc328e9d1f8b8521e664cbe0c061b34fac60d94bfbdbd715fbc42f
catalog      SHA256 6d6bb668130943a891fa0d9add90272398344d6588e3a1c6e0eb55c61cdadddb
```

Worker kiểm tra đủ input 24 unique IDs, exact gold patches và chỉ chọn subset
được assignment cho VM đó. Hash plan trong snapshot phải bằng hash đã giữ từ
build manifest; index phải đúng. Docker image ID, tag và thứ tự diff-ID chain
được kiểm tra trước test; không có container test cũ. Tổng cộng 383 image
metadata thuộc 8 shard được kiểm tra; **chỉ 24 instance được chạy SWE-bench**.
Metadata Docker không đồng nghĩa hash lại mọi byte của root filesystem.

Kết quả hậu kiểm:

- Đủ 24 report, không có report thiếu, thừa hoặc instance trùng giữa nhóm.
- Cả 24 resolved; toàn bộ nội dung JSON đã parse khớp cả hai nguồn report cũ,
  không chỉ so `resolved_count`.
- Cả 8 harness subprocess exit 0; mỗi worker có summary complete.
- Cả 24 image receipt có `source=local`; `cache_added_bytes=0` cho tất cả.
  Tổng image-ensure metadata time: **0.42544649199999895 giây**.
- Không có registry credentials trong scoring VM theo cấu hình và kiểm tra
  env key của worker. Không có cache Volume trong lệnh tạo VM.
- Đủ 8 VM ID được terminate; các lần collect billing dùng SDK xác nhận app
  không còn VM. Exit code coordinator là **0**.
- Full suite offline sau thay đổi: **106 test pass** (50 root + 56 probes).
  Đây là unit test, tách biệt với 24 phép chấm thực trên Modal.

[comparison.json](comparison.json) được tạo lại bằng collector offline kiểm
tra input hash, evaluator source, source readback từng VM, từng image/report,
cleanup và billing; không chỉ sao chép trường `report_parity` của coordinator.

## Chi phí và độ trễ billing

| Resource provider báo cho app này | USD |
|---|---:|
| CPU | 0.30019814 |
| Memory | 0.20309388 |
| Network Egress | 0.00003775 |
| **Tổng compute/egress** | **0.50332977** |

Lần đọc đầu lúc 07:11:04 UTC+7 chỉ báo **$0.42720029**. Lần đọc lúc 07:12:49
và 07:13:55 đều báo **$0.50332977**. Đọc bổ sung lúc **07:17:29 UTC+7** vẫn
cho cùng số tiền: ba lần đọc cuối khớp, hai bản cuối cách nhau **213.807212 s**.
Các bản đọc được giữ nguyên để thể hiện độ trễ. Collector yêu cầu các lần đọc
ổn định cách nhau ít nhất 60 giây, có billing rows dương và không còn VM chạy.
`comparison.json` chỉ rõ hai bản đọc cuối dùng để chốt số.

Tiền ở đây là usage provider **trước credits**, không phải invoice cuối hoặc
credit balance. Nó không gồm GCP coordinator, shared storage, phí lưu snapshot
hoặc chi phí chuẩn bị/build cache từ trước. Phí lưu Image snapshot 90 ngày vẫn
chưa được xác nhận; không suy rằng khoản đó bằng 0 từ app billing này.

Đây là **chi phí lượt eval mới theo yêu cầu hiện tại**, tách với ngân sách nạp
môi trường một lần trước đó. Lượt build catalog 500 trước đây gồm cả lần lỗi
đã báo $1.09802332; không cộng nó vào chi phí mỗi lượt chấm ở bảng so sánh.
Không có retry compute, chạy full 500 hay build snapshot thêm trong lượt này.

## Diễn giải thời gian và giới hạn suy rộng

Tổng Docker-ready time của 8 VM là **107.38642856106162 s**, so với
**12.668885851278901 s** của VM snapshot pilot cũ. Tổng thời gian tám harness
CLI subprocess mới là **354.813220954 s**; ba subprocess cũ là
**132.770159743 s**. Các số này được lấy từ cùng marker `SWE_TEST_SECONDS`.
Khoảng còn lại trong wall mới gồm source/input transfer, preflight, image
cleanup, thu artifact, termination và SDK/coordinator overhead.

Quan sát phù hợp với việc chia 24 bài thành nhiều nhóm nhỏ làm tăng số lần
khởi động và giảm khả năng ghép các bài chạy song song. Tuy nhiên chưa có
ablation tách riêng scheduling, lazy filesystem I/O, cold cache hay biến thiên
host. Không được gán toàn bộ chênh lệch cho một nguyên nhân duy nhất.
`image-ensure` gần 0 chỉ nói rằng không pull/load image; filesystem snapshot
vẫn có I/O khi test truy cập file, và thời gian đó nằm trong wall/billing.

Nếu mục tiêu là luôn lặp đúng bộ 24 này, snapshot gộp riêng 24 từng cho số đo
tốt hơn. Bộ 11 shard đã chuẩn bị cho toàn bộ 500 môi trường; lợi ích amortize
startup có thể khác khi mỗi VM xử lý nhiều bài hơn. **Chưa đo full 500**, nên
không nhân chi phí này với 500/24, không hứa mức tiết kiệm full Verified và
không dùng gold controls như model score. Mỗi cấu hình hiện mới có một lượt đo,
không có error bar hoặc kết luận thống kê qua các lần lặp.

## Artifact và tái lập

- [comparison.json](comparison.json): parity, chi phí, timing và config.
- [receipt.json](receipt.json), [plan.json](plan.json),
  [source-context.json](source-context.json): VM/snapshot IDs và provenance.
- [reports.json](reports.json): đủ 24 report chính thức của lượt mới.
- [image-checks.json](image-checks.json),
  [source-readbacks.json](source-readbacks.json): receipt chọn image và hash
  sáu file trên từng VM.
- [billing-first.json](billing-first.json),
  [billing-second.json](billing-second.json),
  [billing-third.json](billing-third.json),
  [billing-final.json](billing-final.json): các lần đọc provider và cleanup.
- [offline-tests.json](offline-tests.json),
  [preflight-unit-tests.txt](preflight-unit-tests.txt): unit validation riêng.
- `raw-evidence.tar.gz`: input đóng băng, per-shard reports/test logs, assignment,
  image/source checks, receipt và console gốc. Không có binary Docker image
  hay credentials; `SHA256SUMS.txt` xác định các byte được bàn giao.
- [Hướng dẫn runner](../../probes/SHARDED_PILOT.md),
  [audit trước lượt này](../2026-10-22-scientific-audit/README.md).

Để tái kiểm tra bản raw đã giải nén bằng Python stdlib sẵn có, không tạo VM:

```bash
python probes/summarize_sharded_pilot.py --out PATH_TO_EXTRACTED_RUN \
  --billing-name billing-final.json --previous-billing billing-third.json
```

Lệnh trên đối chiếu hai lần đọc cuối. Không chạy lại `run` chỉ để đọc report/billing;
chấm mới là một allocation mới và phải có run directory mới cùng budget guard.
