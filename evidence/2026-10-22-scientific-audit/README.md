# Audit khoa học và kỹ thuật: SWE-bench Verified trên Modal VM

Ngày báo cáo: **22/10/2026, UTC+7**. Phạm vi: pipeline chấm hiện có,
pilot so sánh 24 bài và catalog snapshot môi trường cho 500 bài.
Đây là báo cáo audit bằng code, artifact và trạng thái provider; không phải
một lần chạy lại toàn bộ benchmark. File máy đọc: [audit.json](audit.json).

## 1. Kết luận có thể bảo vệ bằng bằng chứng

**Chưa phát hiện sai lệch trong report parity, danh tính image, coverage 500 bài
hoặc các phép tính chi phí được kiểm tra. Có một lỗ hổng validator catalog đã
được tái hiện và sửa trong đợt audit này. Không có cơ sở để cam kết tuyệt đối
“không còn bất kỳ sai sót nào”.**

Kết quả được xác nhận trong đúng phạm vi sau:

| Kết luận | Bằng chứng trực tiếp | Giới hạn |
|---|---|---|
| Có 11 snapshot bao phủ đúng 500 instance đã pin | 500 cặp reference/image ID; từng shard khớp plan, manifest và restore receipt | Kiểm tra metadata Docker trên VM mới; không chạy test SWE-bench của cả 500 |
| Pilot snapshot giữ nguyên kết quả baseline trên 24 gold patch | 24/24 toàn bộ JSON report đã parse bằng nhau; đúng gold input và tập test bắt buộc | Một pilot, gold controls; chưa thử đủ loại model patch |
| Lượt chấm từ snapshot đã có rẻ hơn 82.2620% trong pilot | Billing theo app/resource: $0.98970092 → $0.17555287 | Compute/egress trước credits; chưa gồm phí lưu snapshot, không suy rộng full 500 |
| Wall time mỗi lượt chấm giảm 5.5031 lần trong pilot | 944.6862960755825 → 171.66414378210902 giây | Tính cả chuẩn bị và canary lần đầu thì 970.2423786334693 giây |
| TTL 90 ngày được API chấp nhận và snapshot khôi phục ngay thành công | 11 Image ID, cấu hình `ttl=7776000`, restore receipts | Chưa thể quan sát đủ 90 ngày; không phải bảo đảm dịch vụ/backup |
| Không còn VM của ba app build/probe | Đọc SDK mới lúc 06:04:54 ngày 22/10, UTC+7: cả ba danh sách rỗng | Trạng thái ở thời điểm đọc, không phải toàn account |

**Chưa có score khoa học full Verified từ bộ snapshot mới.** Web/CLI production
vẫn dùng backend trước đó; tạo xong catalog không tự tích hợp nó vào đường chấm.

## 2. Hệ thống đang làm gì

Một instance SWE-bench gồm issue, repository/base commit, patch tham chiếu,
test patch và các danh sách test cần đánh giá. Model cung cấp `model_patch`.
Harness dựng môi trường đúng bài, áp dụng patch và đánh giá bằng test của bài.
Mỗi instance trong catalog này có một reference Docker image riêng. Các image
chia sẻ nhiều layer; 500 reference không đồng nghĩa 500 bản sao đầy đủ của OS.

Snapshot đang dùng là **filesystem snapshot của Modal VM** có sẵn Docker store
`/var/lib/docker`. Nó lưu trạng thái disk trước khi chấm, không lưu tiến trình
test đang chạy hay kết quả model. Lần khôi phục vẫn khởi động Docker và phải đọc
file từ storage; thời gian đó nằm trong wall time/billing đã đo.

| Thành phần | Vai trò | Định danh đã giữ |
|---|---|---|
| Dataset | Đầu bài và protocol benchmark | `SWE-bench/SWE-bench_Verified`, revision `78f471bf655a3137b2e8a75af1501690ec009ec3` |
| Harness | Áp dụng patch, chạy và parse test | SWE-bench `4.1.0` |
| Runtime | OS/Python/Docker đã qualification | `im-xE1XGZwwpKZrzcYW0wqjEI`, Linux/amd64, Docker `20.10.24+dfsg1`, overlay2 |
| Modal client | Tạo VM và filesystem snapshot | SDK `1.6.2`; profile `phamvanvuhoan` |
| OCI cache | Layer nén có digest để dựng lại image | Volume v2 `swebench-verified-images-v1` |
| Snapshot catalog | Chọn snapshot chứa instance cần chấm | 11 Image ID trong [catalog](../2026-10-22-verified-snapshots-90d/usable-shards-catalog.json) |
| Input/output mỗi lần chấm | Prediction, report, log và receipt riêng | Không đóng prediction/kết quả vào snapshot sạch |

Hàm grading chính thức của bản 4.1.0 dùng trạng thái `FAIL_TO_PASS` và
`PASS_TO_PASS` để xác định resolved; phải tuân thủ parser/protocol riêng của
harness, không tự thay bằng “pytest exit 0”. Audit này giữ nguyên evaluator và
đối chiếu output của nó. [Nguồn grading 4.1.0](https://github.com/SWE-bench/SWE-bench/blob/v4.1.0/swebench/harness/grading.py).

## 3. Những bước audit thực sự đã làm

1. Đọc coordinator, worker, cache/restore, đường gọi harness, summary và billing
   guard; đối chiếu source đã chạy với provenance lưu từ VM.
2. Chạy lại 50 unit test ở root và 44 test ở `probes`: **94 test offline pass**.
   Trong đó có 10 test mới so với milestone build, gồm mutation/negative cases.
3. Chạy **16 unit check trên host GCP hiện có**, Python 3.13.7, từ source được
   copy vào thư mục audit riêng. Không cài dependency, không tạo Modal VM mới.
   Đây là unit check trên GCP, không phải 16 phép thử runtime Modal.
4. Viết một auditor stdlib riêng, không import validator/build helper để đưa ra
   kết luận parity/coverage/cost. Nó đọc trực tiếp artifact và hash gốc.
5. Auditor xác nhận **75 checksum** thuộc ba evidence bundle, 10 Git source
   blob trong launch context, 11 bộ plan/manifest/restore receipt và 500 image.
6. Lấy lại hai input pilot từ host GCP: baseline và snapshot dùng cùng byte;
   cả 24 prediction đều đúng bằng gold patch trong 24 dataset row đã đóng băng.
7. So sánh đầy đủ 24 report, không chỉ `resolved`; kiểm tra mỗi danh sách test
   bắt buộc khớp dataset row. Có **87 FAIL_TO_PASS** và **4277 PASS_TO_PASS**
   success entries, failure rỗng. Đây là số entry cộng theo instance, không phải
   số test duy nhất toàn bộ benchmark và không phải số lần chạy mới trong audit.
8. Đọc lại SDK/billing của ba app build/probe, xác nhận cleanup và số tiền
   không đổi so với billing đã ổn định trước đó.

Auditor từ chối duplicate JSON key và NaN/Infinity, thiếu billing row, cost âm,
checksum sai, coverage trùng/thiếu hoặc một shard bị gắn sang nhóm khác.
Checksum chứng minh tính nhất quán của các byte giữ lại; nó không phải chữ ký
độc lập từ provider và không loại trừ lỗi chung giữa nguồn log và chương trình.

## 4. Phát hiện logic và thay đổi trong audit

### 4.1. Lỗ hổng mapping từng shard — đã sửa, không đổi kết quả đã đo

Trong code thực thi build ban đầu, `verify_catalog` xác nhận đủ shard,
`verified`, cleanup và hợp của 500 cặp reference/ID. Nó chưa ràng buộc mỗi tập
image với đúng **index của shard và snapshot** mà catalog công bố.

Tình huống tái hiện: đổi chỗ trường `images` của shard 0 và 1. Cả hai cùng 100
image; hợp toàn cục vẫn đủ 500. Validator cũ trả `True`, dù một consumer có thể
chọn nhầm snapshot cho instance và fail khi thiếu image. Đây là lỗ hổng kiểm
tra metadata, không phải bằng chứng rằng catalog thực tế đã bị đổi chỗ.

Đã sửa validator để yêu cầu index tồn tại và không trùng, count chính xác,
coverage từng shard khớp đúng group trong plan, Image ID có mặt và không bị
dùng lại. Regression test từ chối swap, duplicate index/ID và count sai.
Auditor riêng còn đối chiếu hash của plan cụ thể với manifest và restore
receipt. **Catalog thực tế của cả 11 nhóm đều pass bước chặt hơn này.**

Không thay `core.py`, `remote_worker.py`, parser chính thức, test patch,
prediction, image hoặc report gốc. Không build lại snapshot, không chấm lại
24/500 bài để sửa metadata validator. Fix mới được unit test trên Windows/GCP;
chưa chạy lại coordinator build mới trên Modal vì không cần tiêu thêm compute.

### 4.2. Những điều không được gọi là “đã chứng minh”

- Docker `inspect` sau restore xác nhận ID/tag/diff-ID chain. Nó không hash lại
  mọi byte file trong toàn bộ 500 root filesystem. Lúc chuẩn bị, byte config,
  layer nén và layer giải nén có được hash trước khi nạp Docker.
- `/bin/true` chỉ chứng minh một container đại diện có thể khởi chạy executable;
  không chứng minh dependency/test/data của mọi bài đều hoạt động.
- Launch context giữ hash của 10 source file; source readback trực tiếp sau
  restore giữ 4 file trên **một VM shard**. Không có readback từng source file
  trên cả 22 VM; không nên viết thành “mọi source của mọi VM đều đã đọc lại”.
- 500 IDs được đối chiếu với pinned dataset trong lần build và giữ receipt.
  Audit lần này đối chiếu lại receipt/metadata, không tải mới toàn dataset từ HF.
- Summary không phải nguồn kết quả duy nhất; kết luận parity dựa trên report,
  input và billing rows gốc. Không phát hiện khác biệt trong phần đã đối chiếu.

## 5. Pilot 24 bài: logic, kết quả và khả năng suy rộng

Hai đường VM baseline và VM snapshot dùng cùng 24 input, cùng thứ tự batch,
gold patch, runtime, harness và bốn file evaluator có hash bằng nhau. Cả hai
dùng **16 physical cores / 64 GiB, 8 workers, 3 batch × 8, timeout test 600 s**.
Mỗi lần bắt đầu từ VM mới; snapshot là môi trường trước test, không có test
container cũ. Snapshot scorer thiếu/khác image thì fail, không fallback pull.

Input đã xác minh lại:

```text
pilot.json   SHA256 47b545950e6fc204b2d028573298f4ee65a9497c035012bfe9ffe709c91ddbbf
preds.jsonl  SHA256 aa914e991bbc328e9d1f8b8521e664cbe0c061b34fac60d94bfbdbd715fbc42f
```

| Chỉ tiêu | VM + OCI Volume | VM từ snapshot đã chuẩn bị |
|---|---:|---:|
| Report đủ / gold resolved | 24 / 24 | 24 / 24 |
| Toàn bộ parsed JSON report khớp baseline | — | 24/24 |
| Wall time lượt chấm | 944.6862960755825 s | 171.66414378210902 s |
| Compute/egress trước credits | $0.98970092 | $0.17555287 |
| Docker image source | Nạp 24 từ Volume | 24 local, 0 byte cache thêm |

Tính lại trực tiếp:

```text
speedup = 944.6862960755825 / 171.66414378210902 = 5.503107843386678
saving  = (0.98970092 - 0.17555287) / 0.98970092
        = 82.26202820949181294082256688%
```

Baseline mất khoảng 756.05 giây nạp image, xấp xỉ 80% wall time trong lần đo.
Đưa bước đó sang một VM chuẩn bị nhỏ giải thích được hướng giảm thời gian/tiền.
Đây là diễn giải cơ chế phù hợp số đo, không phải một ablation loại bỏ mọi
nguồn nhiễu: trạng thái host, storage cache, tải nền và vị trí chạy có thể khác.

Chuẩn bị pilot mất 792.3195255920291 s, chi phí $0.10414732. Canary thêm
$0.00026427. Tổng lần dùng đầu **$0.27996446**, giảm 71.7122% so với baseline,
nhưng tổng thời gian canary + prepare + scoring **970.2423786334693 s**, dài hơn
baseline khoảng 25.56 s. Vì vậy “nhanh 5.5 lần” chỉ đúng cho lượt chấm khi đã
có snapshot. Điểm hòa vốn một lượt chỉ áp dụng chi phí pilot đã đo, chưa tính
storage và không phải điểm hòa vốn cho toàn bộ quá trình tạo catalog 500.

Mẫu được chọn gần đều theo repo bằng SHA256 ranking, gồm 12 repo. Dataset đầy
đủ có 231/500 bài Django nhưng mẫu chỉ có 3/24 bài Django. Không thể lấy
`500/24 × chi phí pilot` làm estimate khoa học chắc chắn cho full Verified.
Chưa có lặp nhiều lần, random hóa thứ tự baseline/snapshot, error bar hoặc
confidence interval có ý nghĩa. 24 gold success là positive controls cho
environment/parity, **không phải model đạt 100%**.

### So với `--modal true` của upstream

Pilot upstream ghi $0.46610054, 22 report, 20 resolved, 2 unresolved và 2 thiếu
report; VM baseline ghi $0.98970092 và 24/24 gold resolved. Bốn điểm khác là
`astropy__astropy-8707`, `matplotlib__matplotlib-25122`,
`pylint-dev__pylint-4604`, `pydata__xarray-6938`. Diagnostics ghi build thiếu
`pkg_resources`, timeout có tính cold build, và hai required-test failure chưa
xác lập nguyên nhân gốc. Không tự quy mọi khác biệt cho một nguyên nhân chung.

Số tiền native thấp hơn trong phép thử đó, nhưng output chưa tương đương.
Do đó chưa có bằng chứng “native rẻ hơn mà chính xác tương đương” hoặc kết luận
một backend luôn tốt hơn backend kia.
[Diagnostics và pilot](../2026-10-22-cost-pilot/README.md).

## 6. Catalog 500: quá trình tạo và xác minh

Lượt đầu đã nạp đủ 500 image vào một VM, root dùng khoảng 301.13 GiB. Bước
snapshot trả `InternalError`, code `J5RJJ4HP`; không nhận được usable Image ID.
Đã giữ log Docker shutdown/sync và receipt lỗi. **Chưa xác định giới hạn
size/inode hay nguyên nhân provider**, nên không gọi đây là một giới hạn 300 GiB.
Probe nhỏ sau đó nhận snapshot TTL 90 ngày thành công; điều đó chỉ loại được
giả thuyết TTL 90 ngày luôn không được API hỗ trợ trong bối cảnh này.

Phương án phục hồi tạo 11 VM builder độc lập từ runtime gốc, lưu và thử restore
từng nhóm trước khi sang nhóm tiếp theo. Parent không kế thừa data của shard
trước. Tối đa 100 image hoặc 35 GiB unique expanded layer tar mỗi shard; đây
là lựa chọn của planner, không phải giới hạn Modal công bố. Archive nạp Docker
chuẩn, đầy đủ, gộp tối đa 25 image/24 GiB unique layer tar mỗi batch.

Các lớp kiểm tra: config digest khớp image ID; config rootfs khớp diff-ID list;
SHA-256 layer nén/giải nén khớp; manifest cache không đổi sau plan; Docker inspect
khớp ID/tag/thứ tự layer; builder sạch; dừng Docker rồi sync trước snapshot.
Restore trên VM mới không gắn cache Volume, không truyền registry credentials.
Sau startup checks phải không còn container cũ. Không đưa gold prediction vào
11 snapshot môi trường. Mỗi shard chỉ chứa nhóm image của nó và runtime chung.

| Shard | Số image | Root dùng, GiB | Docker ready restore, s |
|---|---:|---:|---:|
| 0 | 100 | 33.51 | 13.53 |
| 1 | 100 | 28.69 | 18.74 |
| 2 | 59 | 37.27 | 14.19 |
| 3 | 8 | 35.78 | 16.58 |
| 4 | 9 | 36.28 | 12.03 |
| 5 | 9 | 32.54 | 15.31 |
| 6 | 27 | 36.16 | 14.39 |
| 7 | 21 | 37.63 | 10.97 |
| 8 | 43 | 36.92 | 10.40 |
| 9 | 100 | 35.48 | 11.26 |
| 10 | 24 | 9.26 | 5.33 |

Tổng: **500/500**, không trùng/thiếu; 22 builder/restore VM riêng biệt đã tắt;
22 container startup checks bao phủ 12 repo. Tổng wall chuẩn bị:
**5408.518027547747 s**, tức 90 phút 8.5 s. Docker ready 5.33–18.74 s là thời
gian startup ở cấu hình 2 cores/4 GiB, **không phải thời gian eval full shard**.
Chưa chạy lại 24 bài bằng bộ 11 shard mới; 24 image ID của pilot đều khớp
catalog 500, nhưng bằng chứng report parity hiện thuộc snapshot pilot 24 riêng.

Plan gốc SHA-256:
`2a3831d16e29024f85e732f53200ea863917d68acaf207bcdbdb859c02a59dc3`.
Dataset JSON serialization SHA-256:
`6a6fb72484bfc40f35cecbae1ae4615e9460905f729c57659df5f4ef9d7dec6e`.
`dataset-coverage.json` còn có một compact-JSON checksum khác; serialization
khác cho hash khác, không được so hai hash đó như cùng một byte artifact.

## 7. Retention và storage: điều biết và chưa biết

Đã truyền `ttl=90*86400=7776000` cho 11 snapshot. **90 ngày không phải lúc nào
cũng bằng ba tháng lịch**. Deadline bảo thủ của cả bộ là trước
**20/01/2027 04:23:45 UTC+7**, lấy snapshot đầu tiên; các shard tạo sau hết hạn
muộn hơn. Đọc lại không gia hạn TTL. Tài liệu cho phép custom TTL filesystem
snapshot, mặc định 30 ngày; memory snapshot có policy khác và không dùng ở đây.
[Modal snapshot retention](https://modal.com/docs/guide/sandbox-snapshots).

Chưa có quan sát qua đủ 90 ngày, kiểm thử recovery sau sự cố provider hoặc
backup ngoài Modal của filesystem snapshot. Giữ OCI cache và pinned manifest
cho phép xây lại môi trường, có thể tốn compute/I/O lần nữa. Tham chiếu Image ID
đơn thuần là lazy; nó không chứng minh image còn tồn tại nếu chưa tương tác.

Tổng root usage của 11 VM là **386013855744 byte = 359.50341796875 GiB**.
Số này cộng cả runtime/base/layer lặp giữa shard, không phải unique physical
snapshot storage. Đặc tính lazy I/O/dedup/overhead và cách provider tính phí
không thể suy từ `disk_usage`.

Giá công khai lúc audit niêm yết Volume $0.09/GiB/tháng, kèm 1 TiB/tháng miễn
phí; dòng đó không xác định đơn giá filesystem snapshot Image. **Phí lưu 11
snapshot trong 90 ngày chưa được xác nhận.** Không có cơ sở chốt nó miễn phí,
cũng không có cơ sở áp $0.09 × 359.50 × 3 để báo tiền. Cần một quy định billing
hoặc xác nhận cụ thể của provider nếu khoản này quyết định ngân sách.
[Bảng giá Modal](https://modal.com/pricing).

## 8. Kiểm toán chi phí và ngân sách

| Khoản | App/phạm vi | USD trước credits |
|---|---|---:|
| Một VM gộp 500, lỗi ở bước snapshot | `ap-Y9vnazqiWSp4ZjXruAY36O` | 0.53845001 |
| Probe TTL nhỏ | `ap-BAVKD0vLxANkKfT3T4uDxJ` | Provider báo 0E-8 |
| Build + restore 11 shard thành công | `ap-Jq1jUAHsyZmYDlnzOcGG0h` | 0.55957331 |
| **Build 500 lần này, gồm lượt lỗi** | Tổng ba dòng trên | **1.09802332** |
| Setup/cache/mock 14 app được liệt kê | Receipt riêng | 0.43935820 |
| Pilot snapshot 24, gồm canary/prepare/score | Ba app pilot | 0.27996446 |
| **Tổng các mục chuẩn bị liệt kê** | Không phải tổng account | **1.81734598** |

Hai lần đọc cuối build shard lúc 22:51:07 và 22:54:43 UTC ngày 21/10 bằng nhau.
Audit đọc lại lúc **23:04:54 UTC = 06:04:54 ngày 22/10 UTC+7** vẫn cho cùng số
tiền. Lần đọc sớm hơn $0.55619739 được giữ nguyên để thể hiện billing lag.
Số 0E-8 của probe rất ngắn không chứng minh probe nói chung hay snapshot miễn phí.

Không cộng baseline $0.98970092 hoặc native $0.46610054 vào subtotal chuẩn bị
ở trên; chúng thuộc thí nghiệm so sánh riêng. Nếu cộng cả hai phép so sánh đó,
tổng **những app đã liệt kê qua các nhóm này** là $3.27314744. Đây vẫn không
phải tổng toàn bộ account. Không cộng baseline hai lần khi đọc billing pilot,
vì billing pilot snapshot cũng giữ lại dòng baseline để đối chiếu.

Ngân sách $2 trước đó được theo dõi trong phạm vi nạp/chuẩn bị đã mô tả.
Số tiền thực đo của các mục chuẩn bị liệt kê nhỏ hơn $2. Nó không bảo đảm toàn
account dưới $2, không gồm phí snapshot storage chưa biết, GCP/shared storage,
credits, thuế hoặc invoice cuối. Có hai app web control trong khoản 14 app;
nếu chỉ lấy scope cache/mock cũ thì $0.42199984. Báo cáo dùng khoản rộng hơn
$0.43935820 để không bỏ sót chúng trong subtotal đã liệt kê.

Build shard chốt CPU/RAM request bằng limit ở 2 cores/4 GiB, một VM hoạt động
mỗi thời điểm, tổng deadline coordinator 5700 s, lifetime mỗi VM hữu hạn.
Với rate đã kiểm tra, CPU/RAM envelope là $0.601464, estimate có margin $0.65.
Rate Sandbox là $0.00003942/core/s và $0.00000667/GiB/s; physical core tương
đương 2 vCPU. Đây là envelope theo cấu hình và luồng bình thường; **không phải
provider hard cap** trước lỗi RPC/termination, storage/egress hoặc giá thay đổi.
[Sandbox resources](https://modal.com/docs/guide/sandbox-resources),
[Sandbox rates](https://modal.com/pricing).

Guard dùng reported usage theo tháng lịch và giữ estimate của job mới kết
thúc thêm 10 phút để đợi billing. Nó không phải credit balance chính xác,
không biết hết job ngoài ledger và không thay thế giới hạn billing provider.
Không mở lại profile `kieusontung8`, không đổi spend limit trong audit này.

## 9. Contract chấm điểm và giới hạn xác nhận

Static review và các test hiện có xác nhận đường CLI chấp nhận JSON/JSONL/CSV,
từ chối duplicate/unknown IDs, yêu cầu full mode có đúng 500 prediction theo
pinned dataset, và giữ empty patch trong mẫu số. Missing report của patch
không rỗng làm run incomplete; subset không được gọi là full Verified score.
Full score theo contract wrapper là `resolved/500` khi đủ report/empty cases.
CLI còn yêu cầu harness exit code 0 để báo thành công. Khi sử dụng khoa học,
phải đọc cả trạng thái run/exit và report, không lấy riêng một trường số.

Đây là contract của wrapper đang giữ để tránh công bố score khi hạ tầng thiếu
report; không tự suy nó giống mọi aggregate/error convention của leaderboard
upstream. JSON report equality cũng không chứng minh harness chính thức không
có lỗi parser. Nghiên cứu phải ghi rõ harness version, dataset pin, timeout,
prediction hash, empty/missing/errors và mọi can thiệp môi trường.

Để nâng mức bằng chứng trước khi dùng snapshot mới cho kết quả model công bố,
cần kiểm chứng end-to-end đường snapshot consumer với tập 24 cố định, thêm
negative/empty/invalid patch và timeout controls ở nhiều repo, rồi chạy model
prediction full 500 khi có ngân sách cho chính lượt eval. Nếu công bố tối ưu
performance, cần các cặp chạy lặp với thứ tự được kiểm soát và báo phân bố,
không chỉ một tỉ lệ. Những bước đó **chưa được thực hiện trong audit này**.

## 10. Provenance, tái kiểm tra và Git

Milestone nguồn build thực thi được checkpoint thành
`e0ffeaea19e51c7b2e0f5d3ccc44493b6aface22`. Khi chạy cloud trước đó, Git commit
bị GPG/Pinentry chặn; vì vậy launch đã dùng audited staged context từ base
`b48669c5543c5ccbc46f0fa5cd6dd8c186d3ce2d`, diff SHA-256
`6ae27cb42626d562b81f904bd94ef2467bf44d775be50f0ef3148590468ac9f2`.
Auditor xác nhận cả 10 Git blob trong checkpoint bằng đúng hash launch context.

Sau khi bạn cho phép, commit milestone dùng `git -c commit.gpgsign=false ...`.
Giữ nguyên `commit.gpgsign=true` trong cấu hình. Các cờ `signing_pending`,
`new_code_pushed=false` trong evidence build là **trạng thái lịch sử lúc tạo
artifact**, được giữ để không sửa lại provenance. Tình trạng Git sau audit
được ghi ở commit/report bàn giao; code fix validator sau build không được
trình bày như source đã chạy tạo snapshot. Version production vẫn 0.3.1.

Chạy lại offline bằng Python stdlib sẵn có, từ root repo, không cài dependency:

```powershell
python -m unittest discover -q
python -m unittest discover -s probes -q
python probes/audit_snapshot_evidence.py `
  --source-ref e0ffeaea19e51c7b2e0f5d3ccc44493b6aface22
```

Các lệnh này không tạo VM, không pull image, không chạy full benchmark. Chạy
bằng interpreter Python 3.12 đã có trên Windows; unit checks GCP dùng 3.13.7;
Python runtime trong VM pilot là 3.11.12. Không nhầm ba môi trường này.

Artifact chính:

- [audit.json](audit.json): kết quả rederive và inventory chi tiết, không chỉ summary.
- [pilot-inputs.tar.gz](pilot-inputs.tar.gz): đúng 24 dataset rows và gold predictions
  đã đóng băng; dùng để qualification, không phải output của model cần đánh giá.
- [input-and-unit-verification.json](input-and-unit-verification.json) và
  [remote-unit-tests.txt](remote-unit-tests.txt): byte equality, gold matching,
  hash source và 16 host-GCP unit checks.
- [live-readback.json](live-readback.json): cleanup/billing đọc mới, không có token.
- [offline-test-results.json](offline-test-results.json): output/exit code của
  94 test offline và hash source đã test.
- [Bằng chứng pilot](../2026-10-22-vm-snapshot/README.md),
  [bằng chứng 500](../2026-10-22-verified-snapshots-90d/README.md): receipts,
  reports, plan, context, raw metadata và checksum cũ được bảo toàn.
- `SHA256SUMS.txt` của thư mục này xác định byte của báo cáo và artifact audit.
  Text artifact audit dùng UTF-8/LF và được đối chiếu từ Git blob sau stage để
  tránh checksum khác do newline Windows bị Git chuẩn hóa lúc commit.

Raw test/log archive cũ còn ở các thư mục `runs/cost-pilot-20261022-01` và
`runs/snapshot-pilot-20261022-01` trên workspace, ngoài Git. Docker image nhiều
GiB vẫn ở Modal; báo cáo không tải image về laptop. Quét pattern credential
trong staged payload/archive không thấy hit; điều đó không phải chứng minh
tuyệt đối không có mọi dạng dữ liệu nhạy cảm.
