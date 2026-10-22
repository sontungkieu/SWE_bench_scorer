# Điều tra bất định: Matplotlib và Requests

Ngày 22/10/2026, giờ Việt Nam. **Chưa sửa tận gốc hai failure lịch sử và
không tuyên bố benchmark đã deterministic.** Đã hoàn tất một protocol chẩn đoán
cố định trên hai VM, cải thiện cách quan sát để giảm sai lệch do profiler, và
thêm chốt về độ tin cậy cho research entry point. Các report cũ giữ nguyên.

## Kết quả thực nghiệm mới

Mỗi issue có một VM mới, restore snapshot N7merge đã kiểm chứng, 16 core/64 GiB,
8 worker, timeout 600 giây. Mỗi VM chạy hai full suite nguyên bản rồi hai full
suite có plugin quan sát; mỗi lần dùng container mới. Không chạy đến khi pass,
không chọn repetition tốt nhất, không chấm lại 24 hay 500 bài.
Mỗi official invocation chọn một ID dù giữ max_workers=8; không có companion
Django hoặc hàng đợi nhiều bài như lần N7. Đây là chẩn đoán cô lập, không phải
tái lập toàn bộ lịch/tải N7 hay bằng chứng rằng concurrency không liên quan.

| Issue | Official gold controls mới | Required test trong hai lượt quan sát |
|---|---|---|
| `psf__requests-1921` | 2/2 resolved | 2/2 pass |
| `matplotlib__matplotlib-20859` | 2/2 resolved | 2/2 pass |

Các lần pass mới không phủ định failure cũ. Requests full suite vẫn có
`test_conflicting_post_params` fail vì cách gọi `pytest.raises` bằng chuỗi
không tương thích pytest 7.4.4. Failure này cũng tồn tại ở baseline cũ, nằm
ngoài required sets, không bị xóa hoặc che đi. "Resolved" của official harness
không đồng nghĩa toàn bộ test được pytest thu thập đều pass.

Source/input được đọc lại từ từng VM và kiểm tra SHA-256. Giữ nguyên bốn
evaluator và các byte eval-script gốc, image ID và gold patch. Lượt observer
chỉ thêm plugin vào invocation pytest; không sửa file test hoặc implementation
trên đĩa. Plugin có bọc method trong memory, nên là **chẩn đoán có can thiệp**,
không được dùng làm official scoring receipt.

## Matplotlib: đã biết cơ chế, chưa bắt được lượt fail với clock đo trực tiếp

[Implementation đúng base commit](https://github.com/matplotlib/matplotlib/blob/64619e53e9d0ed417daba287ac0d3a06943a54d5/lib/matplotlib/legend.py)
phát warning khi default location và elapsed thực tế lớn hơn một giây.
[Test gốc](https://github.com/matplotlib/matplotlib/blob/64619e53e9d0ed417daba287ac0d3a06943a54d5/lib/matplotlib/tests/test_legend.py#L688)
yêu cầu hai warning mà không kiểm soát clock. Test có phụ thuộc tốc độ máy;
container/image hash không khóa thời gian thực thi hay CPU model.

Profiler cũ gắn callback ở nhiều sự kiện có thể làm chậm chính thuật toán đang
đo. Phiên bản mới bọc `_find_best_position`, lấy real clock ngay trước/sau
lời gọi và chỉ ghi log sau khi lời gọi đã kết thúc; không callback bên trong
vòng lặp, không thay clock, không ép warning. Wrapper vẫn có overhead/call-stack
khác, không phải một phép đo hoàn toàn không can thiệp.

VM mới ghi CPU `AMD EPYC 9654 96-Core Emb Processor`. Bốn **outer durations**:
1.496999415, 1.620139860, 1.586795124, 1.549730793 giây. Tất cả default-loc
flag true và target test pass. Đây không phải chính clock sample ở condition;
không trừ số profiler cũ để suy ra overhead chính xác. Không có mẫu dưới 1 giây
hoặc target failure trong lần chẩn đoán này.

Điều đã xác nhận: test chứa dependency thời gian thực, các lần cấp VM có CPU
khác nhau, failure gốc là DID NOT WARN và official parser đọc đúng output.
Điều chưa chứng minh trực tiếp: exact elapsed/locals ở các lần fail lịch sử.
Không quy kết một CPU model cụ thể luôn fail hoặc luôn pass.

## Requests: đã thu được protocol thật, nguyên nhân lịch sử chưa xác định

Container không có `conftest.py`, `httpbin` hoặc `pytest-httpbin`; test dùng
`HTTPBIN='http://httpbin.org/'`. Source `Session.send` được đọc lại, không thấy
một local HTTP mock thay thế tại method được quan sát. Không có local HTTP
fixture trong container được kiểm kê; không suy ra toàn bộ đường mạng vật lý
của provider chỉ từ điều này.

Hai lượt observer ghi đúng chuỗi:

1. GET `/cookies/set?foo=bar` nhận redirect 302, `Set-Cookie: foo=bar; Path=/`.
2. Theo redirect sang `/cookies`, status 200, session jar có `foo=bar`.
3. GET `/response-headers?...expires=Thu,01-Jan-1970...` nhận header xóa cookie;
   session jar hết `foo` và target test pass.

Cookie ban đầu trong các lượt này là session cookie, `expires=null`, không phải
cookie có lifetime một giây. Vì vậy **không có bằng chứng cho cách giải thích
"máy nhanh hơn làm cookie hết hạn"**. Cũng chưa có HTTP status/header/body của
request lỗi lịch sử, nên không được khẳng định nó là HTTP 5xx, rate limit,
proxy cache, lỗi server hay chắc chắn là lỗi mạng. Hai response 302 đúng hiện
tại chỉ chứng minh đường cookie hoạt động trong hai lượt quan sát mới.

Nguồn public HTTP là đầu vào bên ngoài không được snapshot pin. Mỗi instance
được official harness tạo container riêng, không mount một writable testbed
chung; snapshot giữ image trước test. Không tìm được bằng chứng chia sẻ cookie
jar hoặc sửa test từ việc gộp snapshot. Điều này thu hẹp hướng điều tra, chưa
loại được mọi dạng global state trong cùng pytest process của một full suite.

## Phần đã sửa trong công cụ

- `stability_observer.py`: wrapper mỏng thay cho global profiler trong lượt mới;
  giữ return value/exception và restore method sau target test; thu HTTP
  response/redirect/cookie đúng tại request của target. Không ghi env/secrets.
- `stability_audit.py`: phân biệt raw official outcome với độ tin cậy khoa học.
  Hai instance có lịch sử bất ổn làm status `blocked_known_instability`,
  `research_score=null`, kể cả khi lần vừa chạy pass. Không xóa test, không
  giảm mẫu số, không biến failure thành pass. Case khác là `stability_not_assessed`,
  không tự nhận đã certified stable.
- `eval_modes.py` sau run/collect N11/N11max ghi `scientific-validity.json`,
  trả exit 2 khi có known instability; không trả success chỉ vì report đủ hoặc
  lần này khớp baseline. N11 snapshot/catalog và evaluator cũ vẫn giữ nguyên.
- Đây là **sửa lỗi báo độ tin cậy**, không sửa thuật toán của Requests hay
  Matplotlib và không làm test gốc trở nên deterministic. Entry point cũ gọi
  trực tiếp probe hoặc production web chưa tích hợp gate này; không quảng cáo
  như đã triển khai bảo vệ trên toàn hệ thống. N7 vẫn disabled vì parity fail.

`original_reports.json` gồm bốn official reports mới; `history-audit.json`
giữ đánh giá baseline 24, N11, N11max, N7merge, N7weighted và controls mới.
Baseline/N11 từng pass vẫn mang known risk, tránh kết luận sai "N11 ổn định,
chỉ N7 gây flaky". Không có field nào ghi đè official `resolved`.

## Giới hạn có tính nguyên tắc và hướng xử lý

Không thể cam kết "không bao giờ đổi kết quả" cho một assertion phụ thuộc
elapsed thật và một test gọi dịch vụ HTTP công cộng bằng cách chỉ giữ số core,
RAM, image và timeout. Snapshot filesystem không đóng băng hai đầu vào đó.
Thêm nhiều lần pass không biến chúng thành deterministic.

Có hai phần công việc khác nhau:

1. Giữ nguyên official protocol: bảo toàn raw outcomes, thu bằng chứng ở lần
   lỗi thực sự, dùng fixed repetitions/positive-negative controls để định lượng
   độ ổn định và đánh dấu kết quả chưa đủ tin cậy. Không chọn best-of-reruns.
2. Protocol deterministic riêng: test warning phải kiểm soát clock tại phạm
   vi test, kiểm tra cả nhánh trên/dưới ngưỡng; Requests cần HTTP service version
   pin và network fixture được qualification trên toàn required set. Một server
   cookie stub không đủ vì suite còn auth, redirects, HTTPS và các endpoint khác.
   `HTTPBIN_URL` được test hỗ trợ nhưng đổi endpoint vẫn là thay input protocol,
   cần kiểm chứng lại các nhánh URL/HTTPS; không thể gọi tự động là tương đương.

Phương án 2 chưa được áp dụng vì user đã yêu cầu không sửa test/bỏ lỗi. Nếu
chọn nó về sau, phải đặt tên/version protocol riêng, giữ official score song
song, công bố chính xác thay đổi và kiểm tra gold + wrong/empty controls.
Không ép CPU chậm hơn để lấy warning, không suppress warning/failure, không
mock response hay clock trong official score. Chưa có full-500 qualification.

## Chi phí và cleanup

App `ap-XCvA7sURRf8l6O1IVhhooB`, chạy 11:55:15–12:00:12 giờ Việt Nam.
Hai VM cấp tuần tự, cả hai đã terminate; SDK list theo app rỗng ở hai lần
collect. Chi phí mới **$0.30034641** compute/egress trước credits, bằng nhau ở
hai lần đọc cách nhau hơn 60 giây. Cộng thí nghiệm N7 trước **$1.60655749**,
dưới trần $8; không gồm phí lưu snapshot, không phải invoice cuối cùng.

Đã dành lifetime 2900 giây/VM để đủ bốn timeout test 600 giây cộng setup và
collection, không cắt test timeout để ép budget. CPU/RAM + egress reserve
envelope mới $6.21408, cộng chi phí cũ $7.52029108; unused lifetime được trả
lại khi terminate. Thực tế VM dừng sớm khi hoàn thành toàn protocol.

Trước launch, kiểm tra guard OK và worker imports trên runtime có sẵn. Preflight
thử import plugin ở GCP báo thiếu pytest; đây là coordinator không chạy pytest.
Đã kiểm tra cú pháp plugin offline và dùng pytest có sẵn trong Docker image,
không cài dependency local hoặc sửa môi trường coordinator để che lỗi.

Source ban đầu `b999056`, lifetime sửa trước mọi allocation ở `9566a04`.
Hash từng source/input thực tế nằm trong raw evidence. Không retry allocation,
build snapshot mới, đổi production deployment hay tạo GPU job.

## Tái kiểm tra

```sh
python -m unittest discover -s probes -p 'test_*.py'
python probes/stability_audit.py --out PATH_TO_RETAINED_N11_RUN
```

Audit thứ hai chỉ đọc reports/selection rồi tạo `scientific-validity.json`;
exit 2 là deliberate qualification failure, không phải lỗi parser. Giữ nguyên
`reports.json`. Raw evidence gồm cả tám full-suite logs, hardware, source
readbacks, observer JSON, receipt và source/script variants. Checksum các file
public trong `SHA256SUMS.txt`; không dùng những lượt observer làm score.

**112 kiểm tra probe offline pass**, gồm regression cho return/exception và
restore của thin wrapper; official report giữ nguyên; rerun pass không clear
known risk; incomplete/malformed report fail closed; N11 run và billing collect
không trả scientific success chỉ vì subprocess thành công. Đây là test công
cụ, không phải tuyên bố benchmark đã deterministic. Không cài dependency local.
