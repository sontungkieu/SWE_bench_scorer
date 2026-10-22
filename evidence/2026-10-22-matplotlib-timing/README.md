# Vì sao Matplotlib lệch dù giữ cấu hình? Kiểm tra warning theo thời gian

Ngày 22/10/2026, giờ Việt Nam. Phạm vi: `matplotlib__matplotlib-20859` và companion `django__django-16429` của shard 2 gốc. Không chấm lại full 24 hoặc full 500, không sửa benchmark test, không thay score gốc, không build snapshot mới.

## Kết luận và mức chắc chắn

**Đã xác nhận test có phụ thuộc vào thời gian thực, và cùng yêu cầu CPU/RAM có thể nhận CPU khác loại. Giả thuyết “máy nhanh hơn nên thiếu warning” phù hợp mạnh với source và log cũ. Tuy nhiên chưa đo được clock của chính lượt fail, và các lượt chẩn đoán mới chưa tái hiện nhánh dưới 1 giây. Không được gọi giả thuyết đó là nguyên nhân lịch sử đã chứng minh tuyệt đối.**

Tách ba kết luận:

1. **Đã kiểm chứng:** scorer trả đúng theo log. Parse lại output fail bằng official SWE-bench 4.1.0 cho full JSON report giống hệt bản đã lưu; không phải lỗi đếm hoặc parser tự tạo failure.
2. **Đã kiểm chứng:** warning đòi elapsed > 1 giây. Trong 8 lần đo có profiler, elapsed đều khoảng **1.857–1.901 giây**, default loc đúng và không early return; test nhận đủ warning và pass.
3. **Suy luận mạnh, còn giới hạn:** lượt fail cũ không warning vì chạy nhanh qua ngưỡng. Suite cũ nhanh hơn (7.53s so với 13.17s), nhưng tổng suite không cho biết trực tiếp elapsed của `_find_best_position`. Không có clock/locals hay CPU model lịch sử để đóng hoàn toàn các khả năng khác.

Lượt N11max gốc vẫn **23/24 resolved, parity=false, chưa qualification**. Các lượt mới pass không được thay vào report gốc hoặc dùng để nâng kết quả thành 24/24.

## Cơ chế trong source đúng phiên bản

[Matplotlib base commit 64619e5, legend.py](https://github.com/matplotlib/matplotlib/blob/64619e53e9d0ed417daba287ac0d3a06943a54d5/lib/matplotlib/legend.py#L958) chỉ phát warning ở điều kiện:

```python
if self._loc_used_default and time.perf_counter() - start_time > 1:
    _api.warn_external(...)
```

[Test gốc](https://github.com/matplotlib/matplotlib/blob/64619e53e9d0ed417daba287ac0d3a06943a54d5/lib/matplotlib/tests/test_legend.py#L688) tạo nhiều line/legend rồi yêu cầu có hai warning. Hàm tìm vị trí được gọi hai lần. Với default loc và khi hàm đi tới điều kiện này: mỗi lần mất ≤1 giây sẽ không phát warning từ nhánh đó, dù chức năng legend vẫn đúng. Test không khóa elapsed bằng mock clock.

Ngoài ngưỡng thời gian, hàm còn có một early return nếu `badness == 0`. Phép đo mới ghi cả `badness`, số candidates và default-loc flag để phân biệt hai đường. Cả 8 lần quan sát đều có `badness=1000`, 10 candidates, default loc=true, và đi tới clock condition. Điều này loại early return trong **các lượt quan sát mới**, không khôi phục locals chưa được ghi ở lượt fail cũ.

FAIL_TO_PASS chính của issue (`test_subfigure_legend`) vẫn pass ở lượt fail; phần đổi kết quả là PASS_TO_PASS `test_warn_big_data_best_loc`, pytest báo **DID NOT WARN**. Official grading coi PASS_TO_PASS thất bại là unresolved; không được bỏ qua vì nghi là flaky.

## Đã đối chiếu những gì giữa hai lượt cũ?

- Cùng 16 cores/64 GiB mỗi VM, 8 workers, test timeout 600 giây.
- Cùng tập bài trong shard 2, snapshot ID và instance Docker image ID.
- Cùng SHA-256 của 4 evaluator và 2 helper.
- Gold patch và toàn bộ eval script khớp byte; script SHA-256 `f3a432a872953c583ed024a50da0e88a35adad8310f91b43b4269dd0e3f1869a`.
- Các dòng dependency đã cài trong log đều khớp, bao gồm NumPy 1.24.4, Python 3.8.20, pytest 8.3.4, pluggy 1.5.0. Đây là đối chiếu các version được log, không phải full native-binary attestation.
- Chỉ một required test đổi trạng thái; các nhóm FAIL_TO_PASS giữ nguyên.
- Đọc lại log gốc qua `get_eval_report()` 4.1.0 cho JSON giống hệt và `resolved=false`. Xem [parser-recheck.json](parser-recheck.json).

Chi tiết kiểm tra byte và danh sách dependency nằm trong [diagnosis.json](diagnosis.json), phần `historical`. File report cũ được kiểm tra không đổi so với Git commit `6549e48`, SHA-256 blob `13eede1d58b3a58c09373019eab6fa68991a107c41af51e4b946dcf7902e8665`.

## Vì sao giữ config chưa giữ được tốc độ?

Hai VM chẩn đoán yêu cầu cùng 16 cores/64 GiB, khôi phục cùng snapshot, nhưng `/proc/cpuinfo` thực tế cho:

| VM | CPU model được guest ghi nhận | Logical CPUs guest báo | Kernel |
|---|---|---:|---|
| 02 | AMD EPYC 9654 96-Core Processor | 16 | 7.2.9 |
| 03 | Intel Xeon Platinum 8375C @ 2.90GHz | 16 | 7.2.9 |

Đây là bằng chứng trực tiếp rằng yêu cầu CPU/RAM **không khóa CPU model** trong các lần cấp VM này. Số core/RAM cũng không ấn định thời gian từng lời gọi; clock, tải host, cache và dispatch native có thể ảnh hưởng. Chưa đo riêng tác động của từng yếu tố, chưa biết CPU model của VM fail cũ, nên không quy lỗi cụ thể cho AMD hay Intel. Hai allocation là hai VM riêng; không có thông tin xác định vật lý host.

## Thiết kế chẩn đoán đã chạy

Trên mỗi VM 02 và 03, lịch cố định trước khi chạy gồm:

1. Hai lần official harness nguyên bản cho đúng nhóm 2 bài (`matplotlib-20859` + `django-16429`), giữ 8 workers và timeout 600 giây. Mỗi lần dùng container mới, không chọn kết quả tốt nhất.
2. Hai lần full Matplotlib test script có observer. Script gốc được kiểm tra byte-equal với eval script cũ; chỉ thêm plugin vào lệnh pytest. Giữ toàn bộ test suite, gold patch và test_patch chính thức; không deselect test.

Hai VM được cấp **tuần tự**. Đây là chẩn đoán một shard với hai allocation, không phải tái chạy toàn bộ lịch N11max hay kiểm chứng riêng ảnh hưởng cap 2. Các lượt observer không có companion Django chạy cùng và không được dùng làm official scoring receipt.

Observer dùng [CPython `sys.setprofile`](https://docs.python.org/3/library/sys.html#sys.setprofile) để quan sát `c_call` ngay trước, `c_return` ngay sau lời gọi `time.perf_counter` ở điều kiện warning. Nó lấy thêm clock thật ở hai đầu, tạo lower/upper bound quanh sample mà code gốc dùng. Không thay function clock, không gán return value, không đổi warning filter, không monkeypatch implementation Matplotlib.

**Profiler có overhead trên toàn thuật toán.** Khoảng lower/upper hẹp chỉ thể hiện độ rộng bracket quanh sample; không được hiểu rằng tổng overhead profiler chỉ vài microsecond, hoặc lấy số profiler làm timing chính xác của lượt không profiler.

SHA-256 file test và `legend.py` trước/sau mỗi lượt observer đều không đổi, và giống nhau giữa cả bốn lượt. Function source của `_find_best_position` cũng khớp qua bốn lượt, SHA-256 `0a3b87d26498faeed02c805c7d94346ec38acb5c0dcb24bc765fcf96348972f3`. Mã observer và source/input đọc lại từ VM được đối chiếu Git blob trong [source-provenance.json](source-provenance.json).

## Kết quả mới: giữ tất cả lượt, không thay report cũ

- Cả 4 official repetitions mới đều có Matplotlib resolved và không có PASS_TO_PASS failure; Django companion cũng pass.
- Cả 4 profiled full-suite repetitions mới đều pass test warning; mỗi lần có hai clock samples.
- Cả 8 sample đều trên 1 giây, không sample dưới ngưỡng, không early return.

| VM | Repetition profiler | Lần gọi | Lower (s) | Upper (s) | Badness | Test warning |
|---|---:|---:|---:|---:|---:|---|
| 02 | 0 | 1 | 1.867128799 | 1.867132309 | 1000 | pass |
| 02 | 0 | 2 | 1.901404860 | 1.901408530 | 1000 | pass |
| 02 | 1 | 1 | 1.872560060 | 1.872564220 | 1000 | pass |
| 02 | 1 | 2 | 1.879486910 | 1.879491410 | 1000 | pass |
| 03 | 0 | 1 | 1.860360134 | 1.860366002 | 1000 | pass |
| 03 | 0 | 2 | 1.875301714 | 1.875307676 | 1000 | pass |
| 03 | 1 | 1 | 1.869457742 | 1.869463094 | 1000 | pass |
| 03 | 1 | 2 | 1.857005489 | 1.857009783 | 1000 | pass |

Vì không tái hiện được nhánh nhanh trong loạt đo mới, không có bằng chứng trực tiếp cho câu “lượt fail cũ mất X giây <1”. Điều đã xác nhận là cơ chế nhạy thời gian, sự thay đổi CPU model ở cùng resource request, và scorer bám đúng output. Bằng chứng hỗ trợ mạnh giả thuyết tốc độ; phần nguyên nhân lịch sử chính xác vẫn có giới hạn quan sát.

## Chi phí, lỗi script và cleanup

| Attempt | App | Compute + egress USD | Wall (s) | Khoảng đọc billing ổn định (s) |
|---|---|---:|---:|---:|
| 01 | `ap-fCv2l5QvSa6hu9p138plju` | 0.02114638 | 26.958 | 165.3 |
| 02 | `ap-iFBohBtNo31AsWeRAsV5uA` | 0.16071678 | 158.466 | 163.9 |
| 03 | `ap-hre6zuXN0VBBuysSUXHlsz` | 0.17128912 | 169.171 | 80.6 |

Attempt 01 dừng trước mọi test do tôi import sai `setup_logger` từ module utils. Log gốc và chi phí $0.02114638 được giữ nguyên. Đã sửa riêng script chẩn đoán sang module `docker_build`, kiểm tra toàn bộ worker imports bằng runtime SWE-bench 4.1.0 có sẵn trên GCP, rồi mới chạy lại. Không sửa test hoặc source benchmark để vượt lỗi này.

Tổng chẩn đoán (kể cả attempt lỗi) **$0.35315228**; cộng N11max trước đó **$0.81207087**, vẫn dưới tổng trần $2. Trước credits, chưa phải invoice chốt; không gồm giá lưu snapshot/GCP. Billing mỗi app bằng nhau qua hai lần đọc cách nhau ≥60 giây. Cả 3 VM đã terminate và SDK list theo app rỗng; không còn job trả phí của đợt này. Không tạo N16 hoặc chấm full 500.

## Xử lý kết quả khoa học

Giữ N11max gốc ở trạng thái unqualified, giữ `resolved=false` của bài này và lưu mọi repetition như evidence độc lập. Không loại PASS_TO_PASS, ép warning, mock clock, làm chậm máy để lấy pass, nâng timeout, hoặc thay kết quả fail bằng một repetition pass. Chẩn đoán nguyên nhân và chính sách báo score là hai việc riêng; thay benchmark hay chính sách aggregation cần một quyết định riêng và disclosure.

Nếu cần chứng minh trực tiếp nhánh nhanh trong tương lai, phải thu observer ở một lượt thực sự fail (với clock thật và ghi overhead), đồng thời lưu CPU metadata, default-loc flag, badness và warning state. Hiện **chưa chạy thêm allocation ngoài ba attempt đã liệt kê**, không tiếp tục tìm một mẫu fail/pass đến khi vừa ý.

## Bằng chứng và tái kiểm tra

- [diagnosis.json](diagnosis.json): historical audit, tất cả repetition, chi phí và các giới hạn suy luận.
- [raw-evidence.tar.gz](raw-evidence.tar.gz): cả ba attempt, log traceback, log test/harness, script nguyên bản/profile, assignment, input/source hashes, clock observations, receipts và billing.
- [source-provenance.json](source-provenance.json): Git commit đầy đủ theo attempt và đối chiếu source readback.
- [parser-recheck.json](parser-recheck.json): parse lại log fail bằng official harness, không ghi đè report cũ.
- `vm02/03-observations.json`, official repetitions, protocol, hardware, receipts và billing trong thư mục này.
- [offline-tests.txt](offline-tests.txt): 78 probe tests pass; thêm 5 preflight tests và worker import preflight trên GCP. Không cài dependency hay bật Docker local.
- `SHA256SUMS.txt`: checksum byte artifact. Dùng blob Git LF khi kiểm tra sau checkout Windows.

Tái chạy audit offline: `python probes/audit_matplotlib_timing.py --out EXTRACTED_ATTEMPT --result OUTPUT_JSON`. Lệnh này chỉ đọc log; không cấp Modal VM hoặc sửa score.
