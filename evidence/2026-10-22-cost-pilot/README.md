# Pilot chi phí: 24 bài, 12 repo, hai đường scorer

**Trên stack hiện tại, VM là đường đã chấm đúng đủ tập gold này. Native có
chi phí thấp hơn nhưng chưa đạt cùng độ phủ/kết quả.** Không chạy full 500,
không thay bài lỗi, không chạy retry bổ sung và không đổi backend web.

Đo ngày **22/10/2026, khoảng 01:31–01:59 giờ Việt Nam**, profile
`phamvanvuhoan`. Hai đường nhận **cùng 24 gold patch công khai**, từ Verified
revision `78f471bf655a3137b2e8a75af1501690ec009ec3`. Đây là kiểm thử hạ tầng,
không phải model predictions hoặc điểm mô hình.

| Chỉ tiêu | Native `--modal true` + compatibility overlay | Docker trong Modal VM |
|---|---:|---:|
| Bài được chọn | 24 | 24 |
| Official report nhận được | 22 | 24 |
| Gold resolved | 20 | 24 |
| Gold unresolved có report | 2 | 0 |
| Thiếu report | 2 | 0 |
| Wall time | 1,695.0701166279614 s, khoảng 28m15s | 944.6862960755825 s, khoảng 15m45s |
| Compute/egress được ghi nhận | **$0.46610054** | **$0.98970092** |
| Cleanup | App stopped, 0 Sandbox | App stopped, 0 Sandbox |

**Tổng $1.45580146**, trước credits, chưa phải hóa đơn cuối. Hai lần đọc
billing cuối liên tiếp cho cùng số tiền. Các snapshot trước đó được giữ lại
để thể hiện metering có độ trễ. Native CLI trả 0 dù thiếu hai report; không
dùng exit code 0 để tuyên bố qualification pass.

## Thiết kế so sánh

- Chọn trước kết quả bằng SHA-256 của `20261022:instance_id`: hai bài mỗi
  repo; Flask chỉ có một bài, nên lấy thêm một bài Django. Manifest giữ
  nguyên 24 ID, seed, số lượng population và checksum input.
- Native: SWE-bench 4.1.0, Modal SDK 1.6.2, overlay tương thích đã audit;
  tối đa 4 Function container, mỗi Sandbox 4 physical cores cố định,
  RAM request 1 GiB / limit 8 GiB. Function timeout 1,200 s gồm build;
  Sandbox timeout 600 s; coordinator timeout 1,800 s; một lần tạo Sandbox.
- VM: một VM 16 physical cores / 64 GiB, ba batch × 8 workers, lifetime
  1,800 s. Dùng `remote_worker.evaluate` của scorer production, dọn image
  sau từng batch như full scorer. Cả 24 image lấy từ Volume, **0 byte cache
  mới**, không pull registry hay truyền registry credential vào VM.
- Hai app chạy đồng thời. Native không dùng cache Volume của Docker.
  Cache access của VM giữ cùng lock với dịch vụ web.
- Guard pass trước cả hai lượt, dự trù $3 native + $2 VM. Dự trù không
  phải provider hard cap; resource policy và timeout được ghi riêng.
- Native sửa một file backend trong bản sao package; test specifications,
  script tạo test, parser và grading không sửa. Source hash của VM được
  đọc trực tiếp từ Sandbox đang chạy; source của coordinator cũng được
  ghi hash và đối chiếu với source đã commit.

## Theo repo

| Repo | Số bài | Native report | Native gold resolved | VM gold resolved |
|---|---:|---:|---:|---:|
| astropy/astropy | 2 | 1 | 1 | 2 |
| django/django | 3 | 3 | 3 | 3 |
| matplotlib/matplotlib | 2 | 1 | 1 | 2 |
| mwaskom/seaborn | 2 | 2 | 2 | 2 |
| pallets/flask | 1 | 1 | 1 | 1 |
| psf/requests | 2 | 2 | 2 | 2 |
| pydata/xarray | 2 | 2 | 1 | 2 |
| pylint-dev/pylint | 2 | 2 | 1 | 2 |
| pytest-dev/pytest | 2 | 2 | 2 | 2 |
| scikit-learn/scikit-learn | 2 | 2 | 2 | 2 |
| sphinx-doc/sphinx | 2 | 2 | 2 | 2 |
| sympy/sympy | 2 | 2 | 2 | 2 |

20 report native gold resolved khớp **toàn bộ JSON đã parse** với report
VM tương ứng. Hai report native unresolved khác verdict của VM, và hai
bài native thiếu report không được quy thành model unresolved.

## Bốn trường hợp native khác VM

1. **astropy-8707:** không dựng được image; log ghi
   `ModuleNotFoundError: No module named 'pkg_resources'` trong build editable.
   VM dùng image đã cache, gold resolved.
2. **matplotlib-25122:** Function đụng timeout **1,200 s**, gồm thời gian dựng
   image; không có report trả về. Đây không phải kết luận rằng test cần hơn
   600 s. Sandbox thực tế tồn tại khoảng 234 s trước cancellation. VM gold resolved.
3. **pylint-4604:** native có report unresolved; test FAIL_TO_PASS
   `tests/checkers/unittest_variables.py::TestMissingSubmodule::test_package_all`
   thất bại. VM pass. Chưa xác lập nguyên nhân gốc của sai khác này.
4. **xarray-6938:** native có report unresolved; test PASS_TO_PASS
   `xarray/tests/test_variable.py::TestNumpyCoercion::test_from_sparse[Variable]`
   thất bại. VM pass. Chưa xác lập nguyên nhân gốc của sai khác này.

Giữ nguyên mọi verdict/log; không sửa test hay dependency giữa lượt đo để
làm các case này pass. Lần thử Django đơn lẻ trước đây không chứng minh
qualification cho các repo khác.

## Diễn giải chi phí và giới hạn

Native tiêu ít hơn trong lượt này nhưng thiếu hai report và có hai gold
unresolved. Vì vậy chưa thể gọi đây là cùng một kết quả chấm với giá rẻ hơn,
hoặc thay VM production bằng native dựa trên số tiền này.

VM mất tổng **756.050970933 s** để nạp image, khoảng **80% wall time**.
VM vẫn giữ allocation 16 cores/64 GiB trong thời gian đó. Cache tránh tải
registry và pin image, nhưng `docker load` vẫn tiêu thời gian compute.

Đây là so sánh hai workflow hiện có: VM đã có đủ 500 image, còn native dựng
nhiều môi trường mới và cũng được hưởng base image/cache Modal từ các lượt
trước. Không phải thí nghiệm hai backend có cùng trạng thái cold cache.
Chi phí cache/mock trước đây khoảng $0.422, storage chung và GCP coordinator
hiện có được tách khỏi compute/egress của pilot này.

Mẫu lấy gần đều theo repo, trong khi Django chiếm 231/500 bài. Không nhân
thẳng chi phí trung bình của 24 bài với 500 để coi là báo giá full scorer.
Gold controls cũng không đo trường hợp model patch gây hang hoặc timeout.

**Quyết định đề xuất:** giữ VM làm scorer chính trên stack hiện tại; native
cần xử lý bốn trường hợp trên trước khi đánh giá lại độ tin cậy và chi phí.
Kết quả không khẳng định mọi phiên bản native Modal đều có các lỗi này.

## Bằng chứng và tái lập

- [verification.json](verification.json): checks, source checkpoint và tổng chi phí.
- [comparison.json](comparison.json): kết quả/timing từng bài, số lượng population và sample.
- [billing.json](billing.json): provider rows được lọc đúng hai app, snapshot cuối.
- [final-cleanup.json](final-cleanup.json): hai app stopped, tasks 0, danh sách Sandbox rỗng.
- [source-provenance.json](source-provenance.json): hash source thực sự chạy.
- [native-reports.json](native-reports.json), [vm-reports.json](vm-reports.json): report nguyên bản gộp theo ID.
- `native-console.log.gz`, `vm-console.log.gz`: log đầy đủ; policy và patch cũng được nén để giữ nguyên byte.
- `SHA256SUMS.txt`: kiểm tra nội dung file bằng chứng sau Git checkout.

Full archive giữ tại `runs/cost-pilot-20261022-01/cost-pilot-evidence.tar.gz`;
không chứa dataset full hoặc credential. Snapshot billing cuối được đọc
thêm sau khi tạo archive và lưu riêng, cùng số tiền ở hai lần đọc cuối.

[Cách chạy và giới hạn pilot](../../probes/COST_PILOT.md).
[Chat VM được dùng làm đối chiếu](codex://threads/01a15dee-0a45-7163-8f70-1fe28904192d).

App native: [ap-KduJ8ShSSodr8wFg0vMZuF](https://modal.com/apps/phamvanvuhoan/main/ap-KduJ8ShSSodr8wFg0vMZuF).
App VM: [ap-WLV8aqDSJDd2u8Pn1JX2mp](https://modal.com/apps/phamvanvuhoan/main/ap-WLV8aqDSJDd2u8Pn1JX2mp).
