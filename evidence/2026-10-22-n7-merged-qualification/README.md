# Build và kiểm chứng hai catalog N7, giữ nguyên N11

Ngày 22/10/2026. **Đã build cả hai catalog N7, đủ 500 image mỗi catalog;
sáu snapshot mới đều restore kiểm chứng thành công. Đã chấm đúng 24 gold
controls mỗi mode: N7merge đạt 23/24, N7weighted 22/24. Cả hai chưa đạt report
parity với baseline, không qualified để tuyên bố tương đương khoa học.**

Tổng compute/egress **$1.30621108 trước credits**, gồm lượt đầu dừng, build/
verify và cả hai lượt score, dưới trần **$8** đã duyệt. Hai lần collect lúc
04:23 và 04:28 UTC cho cùng số tiền. Đây chưa phải final invoice; không gồm
cache/N11 đã trả trước thí nghiệm hoặc phí lưu snapshot tương lai. **Toàn bộ
26 VM của hai lần thử đã terminate; SDK xác nhận cả bốn app không còn VM.**
Không chấm full 500, không sửa benchmark test, không đổi backend web.

## Kết quả và chi phí quan sát

| Cấu hình cùng 24 controls | VM dùng cho mẫu | Resolved / JSON khớp baseline | Score USD | Wall score |
|---|---:|---:|---:|---:|
| Snapshot riêng 24 image, cũ | 1 | 24 / 24 | 0.17555287 | 171.664s |
| N11 tuần tự, cũ | 8 | 24 / 24 | 0.50332977 | 515.249s |
| N7merge | 6 | **23 / 23** | 0.38067735 | 393.333s |
| N7weighted | 6 | **22 / 22** | 0.37222517 | 386.504s |

Mỗi lượt mới đều có đủ 24 report, không thiếu/thừa ID. Chi phí score quan sát
giảm **24.3682%** và **26.0475%** so với N11 tuần tự, nhưng **report parity
fail**, nên không gọi đây là mức tiết kiệm đã qualified cho cùng kết quả.
Snapshot nhỏ cũ chỉ bao phủ 24 image, không phải catalog 500 image. N7 vẫn
chậm hơn snapshot nhỏ vì phải khởi động sáu VM và lặp nhiều nhóm harness.
Không suy rộng tỷ lệ trên mẫu 24 thành chi phí full 500 hay hiệu quả mọi patch.

| Khoản của thí nghiệm hiện tại | USD trước credits |
|---|---:|
| Lượt đầu dừng | 0.08545511 |
| Build + restore verify sáu snapshot mới, cộng cả hai layout | 0.46785345 |
| Score N7merge | 0.38067735 |
| Score N7weighted | 0.37222517 |
| **Tổng** | **1.30621108** |

Build được meter chung một app; không phân bổ giả chi phí build cho từng mode.
Hai app score riêng cho phép so tiền chấm lại trực tiếp. N7weighted ít hơn
N7merge $0.00845218 trong lần đo này; một observation và kết quả khác nhau
không đủ để chọn nó là phương án tối ưu. Mốc score mới đo sau khi app init,
trước app teardown; wall N11 cũ gồm coordinator toàn lượt. JSON giữ cả tổng
VM wall để đối chiếu, tiền lấy từ provider billing thay vì nhân wall với giá.

Recovery bắt đầu **03:24:07.605 UTC**, kết thúc **04:21:15.282 UTC**, tổng
3427.677s. Hai pha score cộng 779.837s; phần còn lại khoảng 2647.840s là
build/verify và coordinator ngoài score. Build là chi phí/thời gian một lần.

## Phạm vi và provenance

- `optimizeN7merge`: `100,100,59,48,43,100,50`; hai nhóm cần snapshot mới,
  năm nhóm giữ snapshot N11; nhóm lớn nhất 95.364 GiB layer tar.
- `optimizeN7weighted`: `100,100,67,36,30,67,100`; bốn nhóm cần snapshot mới,
  ba nhóm giữ snapshot N11; nhóm lớn nhất 63.954 GiB layer tar.
- Mỗi catalog giữ đủ 500 image chính xác một lần. N7 là tổng snapshot cho
  catalog, mẫu 24 bài chỉ dùng sáu snapshot của mỗi mode.
- Giữ N11/N11max và catalog pin cũ. Bốn evaluator gốc giữ nguyên byte:
  `core.py`, `remote_worker.py`, `image_cache.py`, `registry_cache.py`.
- Modal SDK 1.6.2; SWE-bench 4.1.0; `runtime="vm"`, overlay2. Không dùng
  gVisor, không bật Docker local và không truyền registry credentials vào VM.
- Input giữ SHA-256 `47b545950e6fc204b2d028573298f4ee65a9497c035012bfe9ffe709c91ddbbf`
  cho pilot và `aa914e991bbc328e9d1f8b8521e664cbe0c061b34fac60d94bfbdbd715fbc42f`
  cho predictions. Gold patch là positive control, không phải điểm model.
- Source thực thi lần đầu: `10cdd5b299c9da49787aa2bab6073d2ec3d67fad`.
  Source recovery: `ea97319753106cb84318cc67acf4d6cb171a2016`.

## Lượt đầu dừng và sửa builder

Lượt đầu dùng builder 2 core/4 GiB, giới hạn loader 650s. Nhóm gộp đầu nạp
được 30/48 image sau 507.67s rồi loader timeout; chưa tạo snapshot usable,
chưa chạy benchmark test nào. Đây là lỗi dự phòng thời gian build quá chặt,
không phải failure của prediction hay thay đổi kết quả SWE-bench.

Receipts N11 cũ cho thấy nạp khoảng 35 GiB đã mất 390–539s ở 2 core; không
nên áp lại 650s cho nhóm 65–95 GiB. Coordinator sau đó bắt đầu nhóm đầu của
mode còn lại; lượt này được dừng chủ động để đổi cách build. Đã xác nhận không
còn VM thuộc app cũ. Không sửa hay thay report benchmark nào vì chưa có report.

[App cũ](https://modal.com/apps/phamvanvuhoan/main/ap-lTxu3pLpWUdU09qytvNmTJ)
ghi nhận **$0.08545511** compute/egress trước credits trong lần collect đầu.
Khoản này được giữ và cộng vào tổng, không bỏ khỏi chi phí thí nghiệm.
Raw metadata/log ở `first-stopped-evidence.tar.gz`.
Launcher đầu có cảnh báo CRLF ở lệnh `exit` sau khi coordinator đã ghi receipt
và exit-code; launcher recovery dùng LF. Cảnh báo này không thay kết quả hay phí.

Người dùng cho phép một lượt recovery riêng. Builder mới khởi động từ snapshot
N11 sạch có lượng layer lớn nhất trong nhóm, xác nhận base manifest/hash và
image IDs, rồi chỉ nạp image còn thiếu qua loader OCI gốc. Không chép/ghép
thư mục nội bộ Docker. Sau đó xác nhận lại toàn bộ image ID/tag/layer chain
của nhóm trước khi snapshot và sau restore. Không tải lại Docker Hub.

Builder mới 4 core/4 GiB, lifetime 1100s; restore verify 2 core/4 GiB, 120s.
Tối đa sáu build và sáu restore verify, từng VM một. Snapshot TTL 90 ngày;
giá lưu snapshot không nằm trong envelope compute/egress một lần.

CPU/RAM envelope cho recovery là $7.6383504; cộng $0.19 dự phòng cho tối đa
hai builder cũ thành $7.8283504, còn phần nhỏ cho egress dưới trần $8. Đây là
bound theo lifetime/tài nguyên, không phải dự báo invoice hay remaining credit.
[Giá Modal kiểm tra ngày thực thi](https://modal.com/pricing): VM CPU
$0.00003942/core/s, RAM $0.00000667/GiB/s.

## Cấu hình chấm và vì sao 16 core / 8 worker

Mỗi VM chấm vẫn 16 core/64 GiB, tối đa tám bài đồng thời, timeout **600s cho
từng bài**. Worker là một task SWE-bench, không phải CPU core. Một task có thể
tạo nhiều thread/process; không pin cố định mỗi worker vào hai core.
Cấu hình 16/8 được giữ từ baseline để tách ảnh hưởng của snapshot, chưa được
chứng minh là cấu hình rẻ nhất. VM ít bài có thể để core nhàn mà vẫn chịu phí
theo resource request. 8 core/8 worker hoặc 16 core/16 worker cần ablation riêng.

N7merge có một queue chứa chín controls; runner mới giữ một queue chính thức
với `max_workers=8`, không bỏ bài thứ chín hoặc tăng concurrency. Dự phòng
lifetime 1380s cho hai timeout waves; nhóm tới tám bài dự phòng 780s. Deadline
coordinator là 3000s mỗi mode; thiếu đủ dự phòng thì dừng incomplete, không
giảm timeout. VM chạy tuần tự và terminate sau khi thu artifact.

Build và từng mode score có app billing riêng để tách chi phí một lần khỏi
chi phí chấm lại. Mỗi lần chấm dùng VM mới từ filesystem snapshot, không có
cache Volume, không fallback pull/load và không mang test container cũ.

## Vì sao pha score cũ chậm hơn snapshot chỉ chứa 24 image?

Không thể đồng nhất thời gian harness với thời gian chạy test. Parser mới chỉ
đọc các log gốc, không thay scorer hoặc report. Cả 24 eval-script của N11 và
N11max đều khớp byte với snapshot 24-image cũ.

| Khoản đã đo từ log | Snapshot 24 image | N11 tuần tự | N11max cap2 |
|---|---:|---:|---:|
| Nhóm/VM chứa bài thử | 3 batch trên 1 VM | 8 VM | 8 VM |
| Tổng harness CLI, giây | 132.7702 | 354.8132 | 336.6631 |
| Tổng elapsed framework in ra, 24 bài | 112.194 | 128.300 | 107.523 |
| Tổng eval-script ngoài elapsed framework | 155.936 | 200.290 | 164.227 |
| Tổng đuôi cleanup sau bài cuối mỗi nhóm | 45.419 | 121.081 | 121.009 |

Các tổng thời gian từng bài có overlap giữa workers; không cộng các hàng để
suy ra wall time hoặc CPU time. Elapsed framework là thời gian pytest/unittest/
SymPy in ra, có collection/fixtures và không hoàn toàn chỉ là thân test.
Phần còn lại không phải phép đo riêng pip, compile hay I/O.

Ví dụ `astropy-13398` ở N11 tuần tự có eval-script **84.62s**, trong khi pytest
in **2.31s**. Script gốc còn chạy conda/git và `pip install -e .[test] --verbose`
sau khi áp patch. Snapshot đã nạp Docker image chỉ loại bước Docker load;
không loại bước cài/build package trong eval-script cho mỗi patch mới.
Không được xóa bước này để làm benchmark nhanh hơn.

Mỗi container có khoảng 15s ở bước stop. Cleanup chồng nhau trong cùng nhóm,
nhưng cuối mỗi nhóm vẫn phải chờ. Tám nhóm N11 tạo tổng đuôi **121.081s** so
với **45.419s** của ba batch cũ: thêm **75.662s** đo được. VM N11 trong mẫu chỉ
có 1–6 bài, nên còn lặp harness startup và ít tận dụng tám worker hơn.
Full 500 có hàng đợi dài hơn, không được suy rộng hệ số chậm của mẫu 24.

Có thể phân rã chính xác phần tăng của tổng harness CLI
`354.813220954 - 132.770159743 = 222.043061211s` theo các mốc quan sát:

| Khoảng thời gian cộng qua từng nhóm | 24-image cũ | N11 tuần tự | Phần tăng |
|---|---:|---:|---:|
| Từ tạo container đầu đến grade cuối nhóm | 83.887s | 222.664s | 138.777s |
| Đuôi từ grade cuối đến remove cuối nhóm | 45.419s | 121.081s | 75.662s |
| Harness CLI ngoài hai khoảng trên | 3.464160s | 11.068221s | 7.604061s |

Hàng đầu vẫn gồm setup, patch, eval và khác biệt lịch chạy/host; không phải
pure pytest. Hàng cuối là phần dư đo được, không đo riêng từng import hay RPC.
Đây là accounting thời gian, không phải ablation chứng minh ba nguyên nhân
độc lập. Ví dụ minh họa: hai bài mỗi bài mất 10s, chạy chung hai worker và chờ
cleanup 15s mất khoảng 25s; tách thành hai VM tuần tự mất khoảng 50s, dù từng
bài vẫn chỉ chạy 10s.

Một caveat về score: suite Astropy có thể chứa failure ngoài required tests.
Ví dụ log cũ có `test_icrs_cirs` fail, nhưng test này không nằm trong bốn
FAIL_TO_PASS hoặc 68 PASS_TO_PASS của instance đó. Official `resolved` theo
required sets, không phải khẳng định mọi pytest test đều pass. Không sửa parser
hoặc bỏ required failure nào để đạt parity.

CPU model/host, cache và first-touch I/O chưa được kiểm soát hoàn toàn trong
các lượt cũ. Chưa có căn cứ quy toàn bộ phần tăng cho snapshot lớn. N11max
gốc vẫn chỉ 23/24 do Matplotlib warning theo ngưỡng thời gian, không dùng lượt
đó như một phương án tương đương khoa học đã qualified.

## Failure thực tế và kiểm tra scorer

**N7merge:** chỉ `matplotlib__matplotlib-20859` lệch baseline. Required
PASS_TO_PASS `test_warn_big_data_best_loc` báo `DID NOT WARN`; framework in
`1 failed, 88 passed, 10 skipped in 7.65s`. CPU guest ghi nhận AMD EPYC 9655.

**N7weighted:** cùng Matplotlib test đó fail; framework 7.91s, CPU AMD EPYC
9655P. Thêm `psf__requests-1921` fail required PASS_TO_PASS
`test_cookie_removed_on_expire`. Các FAIL_TO_PASS của hai instance vẫn pass.
Không thay test, bỏ required failure, hay thay report để biến 23/22 thành 24.

Matplotlib phù hợp với độ nhạy ngưỡng thời gian 1s đã phân tích trước đó.
Không ghi clock bên trong `_find_best_position` trong lượt chính thức này, nên
không gọi nguyên nhân lịch sử là đã chứng minh tuyệt đối chỉ từ CPU model.
[Chẩn đoán trước và giới hạn](../2026-10-22-matplotlib-timing/README.md).

Requests fail **ngay ở assert đầu tiên sau request set-cookie**, cookie jar
không có `foo`, dẫn đến `KeyError`. Test chưa đến bước gửi request xóa cookie
theo expiry; không được quy lỗi này thành cùng nguyên nhân thời gian của
Matplotlib. [Source đúng base commit](https://raw.githubusercontent.com/psf/requests/3c88e520da24ae6f736929a750876e7654accc3d/test_requests.py)
cho thấy test gọi HTTPbin; URL lấy từ `HTTPBIN_URL`, mặc định HTTPbin công khai.
OCI config 4215 byte được đọc qua Volume API, kiểm tra SHA-256 đúng image ID,
và chỉ trích trường HTTPBIN: image không đặt biến này. Eval-script không export
override. Không tạo VM để đọc config; không in các environment field khác.

**Chưa xác định nguyên nhân gốc Requests:** raw log không lưu response status,
headers, body hoặc redirect history tại request lỗi. HTTP/server/network hoặc
trạng thái cookie là các hướng kiểm tra, chưa phải kết luận đã chứng minh.
Không có trace đủ để khôi phục response sau khi VM bị hủy. Cần một protocol
chẩn đoán riêng nếu muốn đóng nguyên nhân, không retry đến khi pass và lấy
kết quả thuận lợi thay report này.

Raw Requests suite còn có `test_conflicting_post_params` TypeError do cách gọi
pytest cũ. Failure đó đã xuất hiện ở snapshot24 và N7merge; nó nằm ngoài required
sets của instance. Failure **mới làm đổi official score** là cookie test trên.
Không lấy tổng số raw pytest failures để thay định nghĩa resolved chính thức.

Kiểm tra độc lập bằng `get_eval_report` của SWE-bench **4.1.0** trong runtime
sẵn có trên GCP: **48/48 full report JSON tái tạo từ raw output khớp cả file
report từng case và bản collector**. Đây là re-parse, không chạy lại test hay
Modal VM. SHA-256 grading source:
`88fc500ebaf692a53457149cec24dcf82dfa2d48f98021a894a087d275f61df4`.
Chi tiết ở `parser-recheck.json`; không tìm thấy lỗi đếm/gom report của scorer.

## Phép đo mới và giới hạn

Worker N7 ghi Docker events, CPU model và guest counters trước/sau ngoài test
process; không patch clock, không dùng profiler bên trong test. Observer vẫn
có overhead nhỏ chưa tách riêng. Guest counters không đo trực tiếp provider
physical I/O. Phân tích pha tiếp tục dựa trên timestamp harness và elapsed
framework; raw event log được giữ để đối chiếu độc lập.

| Khoản quan sát | Snapshot24 | N11 tuần tự | N7merge | N7weighted |
|---|---:|---:|---:|---:|
| Tổng harness CLI, s | 132.770 | 354.813 | 267.879 | 251.369 |
| Tổng eval-script từng bài, có overlap, s | 268.130 | 328.590 | 275.020 | 265.630 |
| Tổng framework in ra, có overlap, s | 112.194 | 128.300 | 124.941 | 103.723 |
| Tổng phần eval ngoài framework, có overlap, s | 155.936 | 200.290 | 150.079 | 161.907 |
| Tổng đuôi cleanup cuối nhóm, s | 45.419 | 121.081 | 90.783 | 90.835 |
| Tổng Docker ready qua VM, s | 12.669 | 107.386 | 79.240 | 81.690 |

48/48 eval-script mới khớp byte với snapshot24, 12/12 VM score read-back đủ
source/helper hash, 48 image receipt đều `source=local`, không thêm byte cache.
Hai lượt đều đúng 16 core/64 GiB, 8 worker và 600s test timeout; deadline 3000s
không cắt bài nào. `comparison.json` và `phase-analysis.json.gz` giữ số đầy đủ.

Median tỷ lệ eval-script của **cùng từng bài** so với snapshot24 là +1.73%
cho N7merge và +4.98% cho N7weighted, trong khi tổng harness vẫn gần gấp đôi.
Điều này tiếp tục chỉ ra tác động lớn của nhiều nhóm/VM và các pha ngoài test,
không chứng minh từng test đều chậm vì snapshot chứa nhiều image.
Các ratio này cũng chứa required-test failures; execution path có thể thay
đổi khi assert fail, nên chỉ là số mô tả các lượt đã đo, không phải chứng minh
performance trên cùng một kết quả đúng.

Nhóm snapshot lớn nhất 95.364 GiB có Docker-ready khi chấm **12.569s**. Không
có bằng chứng rằng phải tải đủ toàn bộ 95 GiB trước khi chấm. Metadata verify
cộng cả mode chỉ 1.450s và 1.604s; những số này không đo mọi first-touch I/O.

Cùng snapshot N11 gốc 0, cùng bốn controls và cùng resource request, N7merge
nhận AMD EPYC 9654 với VM wall 86.955s; N7weighted nhận Intel Xeon 8375C với
103.314s. Đây là ví dụ biến thiên host/cache ngay ở nhóm không đổi composition;
không quy chênh lệch đó cho cách gộp. Nhiều CPU model xuất hiện trong cả hai
lượt dù request không đổi. Khoảng cách tiền nhỏ giữa hai N7 chưa có độ chắc
thống kê để tuyên bố weighted tốt hơn.

Chỉ có một lượt chính thức mỗi layout trong lần này. Kết quả pass không chứng
minh mọi patch hay mọi lần chạy đều tương đương, đặc biệt với warning nhạy theo
thời gian. Nếu report khác phải giữ nguyên mismatch và unqualified status.

## Kiểm tra offline

Preflight Python sẵn có trên Windows: **96 tests pass**. Trên GCP: 95 pass, một test cần
baseline local được skip; sau đó `prepare --reuse-bases` thực tế chạy thành công
với baseline thật trên GCP. Source archive hash được kiểm tra trước staging.
Đây là kiểm tra code/manifest, không phải kết quả benchmark hay restore receipt.
Sau khi thêm audit và pin catalog, **100 offline tests pass**. Parser recheck
48 case là một kiểm tra thực tế riêng, không được tính thành 48 benchmark pass.

## Artifact, sử dụng và phần chưa đạt

- `optimizeN7merge-catalog.json`, `optimizeN7weighted-catalog.json`: đủ 500 image
  mỗi catalog; SHA được pin trong `eval_modes.py`. Cache usable không đồng nghĩa
  scientific parity qualified. Các snapshot mới có TTL 90 ngày.
- `raw-evidence.tar.gz`: input, manifest/verification, per-case output/report,
  Docker events, host counters, source readback, receipt và hai lần billing.
- `billing-final.json`, `first-billing-final.json`: provider rows/SDK cleanup
  đọc lúc 04:28 UTC; `comparison.json` cộng đủ cả lượt dừng.
- `optimizeN7merge-reports.json`, `optimizeN7weighted-reports.json`: official
  report nguyên trạng; `parser-recheck.json`: tái parse đủ 48 case.
- `SHA256SUMS.txt`: checksum artifact; source thực thi là commit `ea97319`,
  postprocessor và report có checkpoint Git riêng.

N11/N11max và production web giữ nguyên. Hai N7 có status
`snapshots_built_parity_failed`, `scientific_parity_qualified=false`;
`eval_modes.py` tiếp tục chặn chạy chấm tự động qua hai mode đó. Không thay
backend mặc định hoặc chấm full 500 khi positive controls chưa đạt parity.
Snapshot đã được giữ lại, không cần tự build lại trước mỗi lần chấm tương lai.
Trước khi mở mode làm scorer khoa học cần xử lý/kiểm chứng sự không ổn định
của required tests dưới một protocol nhất quán, giữ nguyên benchmark tests.

Phân tích offline có thể tái chạy bằng Python chuẩn, không gọi Modal:

```bash
python probes/summarize_merged_experiment.py --out UNPACKED_RECOVERY_RUN --first-billing FIRST_BILLING_JSON
```

`baseline24-phase-logs.tar.gz` giữ nguyên byte 96 file log/eval/report của 24
controls từ snapshot nhỏ cũ. Hash source archive và tập con được ghi trong
`baseline24-phase-provenance.json`. Các archive N11/N11max đã có trong evidence
trước; không cần thư mục `runs/` riêng của người triển khai để tính lại pha.
