# N11max cap 2: kiểm chứng 24 bài, billing và quyết định về N16

Ngày 22/10/2026, múi giờ Việt Nam. Đây là phép kiểm tra hạ tầng bằng **24 gold patch**, không phải điểm mô hình. **Chưa chấm full 500, chưa build N16, chưa chuyển backend web production.**

## Kết quả thực đo

**Chưa đạt qualification khoa học: 23/24 report JSON khớp lượt N11 tuần tự**, một report khác; thu đủ 24 report, 23 gold control resolved. Chi phí app N11max cap 2 là **$0.45891859**, trước credits. So với N11 tuần tự $0.50332977, mức giảm quan sát được là **8.823%**. Wall time **310.354 giây**, giảm **39.766%** so với 515.249 giây. Có thể báo số đo billing và tốc độ, nhưng **không công nhận mức tiết kiệm này như một backend đã chứng minh tương đương kết quả**.

Bài lệch duy nhất là `matplotlib__matplotlib-20859`: gold patch áp dụng được và FAIL_TO_PASS `test_subfigure_legend` vẫn pass; PASS_TO_PASS `test_warn_big_data_best_loc` fail vì **DID NOT WARN**. Lượt tuần tự có 89 passed/10 skipped trong 13.17s, lượt này có 1 failed/88 passed/10 skipped trong 7.53s ở test suite đó. Harness chính thức vì vậy trả `resolved=false`; coordinator trả exit code 1 do parity fail, không đổi score.

Đã kiểm tra [mã Matplotlib đúng base commit, dòng 958](https://github.com/matplotlib/matplotlib/blob/64619e53e9d0ed417daba287ac0d3a06943a54d5/lib/matplotlib/legend.py#L958): warning phụ thuộc elapsed vượt 1 giây; [test gốc dòng 688](https://github.com/matplotlib/matplotlib/blob/64619e53e9d0ed417daba287ac0d3a06943a54d5/lib/matplotlib/tests/test_legend.py#L688) yêu cầu warning xuất hiện. Gold patch không sửa hàm tạo warning này. Đây là bằng chứng về **test nhạy hiệu năng**, nhưng không có timer trong `_find_best_position` để chứng minh elapsed nội bộ thực tế, và chưa thể quy nguyên nhân cho riêng lịch chạy song song. Giữ nguyên report fail, không loại test, ép warning, đổi CPU để làm máy chậm hoặc chọn lần rerun pass. Chưa chạy lại trả phí. Xem [timing-test-diagnosis.json](timing-test-diagnosis.json) và log nguyên bản trong archive.

Mỗi mode mới chỉ có một lượt đo: **chưa đủ kết luận chạy song song gây ra thay đổi chi phí này**. Khác host, trạng thái cache nền và I/O theo thời điểm đều có thể ảnh hưởng thời gian dù protocol chấm giữ nguyên. Không có randomization, lặp nhiều lần hay confidence interval.

| Đường chạy | Report / gold resolved | Compute + egress USD | Wall giây | Giới hạn so sánh |
|---|---:|---:|---:|---|
| VM + OCI cache cũ | 24 / 24 | 0.98970092 | 944.686 | Cùng input, quy trình nạp image khác |
| Native `--modal true` đã đo trước | 22 / 20 | 0.46610054 | xem evidence gốc | Thiếu 2 report, protocol tài nguyên khác; chưa tương đương khoa học |
| Snapshot chỉ chứa 24 image cũ | 24 / 24 | 0.17555287 | 171.664 | 1 VM và batching 3×8; không đại diện catalog 500 |
| `optimizeN11` tuần tự | 24 / 24 | 0.50332977 | 515.249 | 8 VM cần dùng từ catalog 11 shard |
| `optimizeN11max(cap 2)` mới | 24 / 23 | 0.45891859 | 310.354 | Cùng 8 shard; parity fail ở test warning |

Các giá trên là billing theo app, không gồm chi phí tạo snapshot một lần, storage snapshot hoặc vận hành GCP. Billing đã bằng nhau qua hai lần đọc cách nhau 123.9 giây, nhưng vẫn không phải hóa đơn đã chốt. Chi phí native cũ không được dùng làm bằng chứng thay thế đúng kết quả VM.

## Cấu hình được giữ cố định

- Dataset SWE-bench_Verified revision `78f471bf655a3137b2e8a75af1501690ec009ec3`.
- Input 24 bài SHA-256 `47b545950e6fc204b2d028573298f4ee65a9497c035012bfe9ffe709c91ddbbf`.
- Predictions SHA-256 `aa914e991bbc328e9d1f8b8521e664cbe0c061b34fac60d94bfbdbd715fbc42f`; đều là gold patch cố định.
- 16 physical CPU cores, 64 GiB RAM **mỗi VM**, 8 worker, test timeout 600 giây; lifetime 780 giây mỗi VM. Không giảm tài nguyên hoặc timeout để vừa trần tiền.
- Modal SDK 1.6.2, SWE-bench 4.1.0; runtime `vm`, Docker overlay2. API có tên `Sandbox` nhưng yêu cầu runtime là VM.
- Cùng snapshot IDs và image IDs; không pull registry, không mount OCI cache Volume, không cấp registry secrets cho VM chấm.
- Bốn file evaluator gốc và hai helper worker đã được read-back, so SHA-256 trên **từng VM**. Hai helper giữ nguyên byte so với lượt tuần tự. Coordinator mới chỉ đổi lịch chạy và thêm timer bên ngoài worker.
- Cùng thứ tự đưa nhóm vào hàng đợi: 0, 2, 4, 6, 7, 8, 9, 10; mỗi nhóm dùng cùng tập bài. Hoàn tất từng VM thì thu output và terminate ngay.

Source chạy: commit `c26058d`; manifest ghi full commit và hash archive trong [source-manifest.json](source-manifest.json). App `ap-ieIjh0VdOg7IiggbOW7dh5`, profile `phamvanvuhoan`. Khoảng chạy UTC `2026-10-22T00:47:52.099249+00:00` đến `2026-10-22T00:53:02.453436+00:00`. Tất cả 8 VM đã terminate và SDK list theo app trả rỗng.

## Trần $2 và mức song song thực tế

`N=11` là tổng snapshot/shard cho toàn bộ 500 image; pilot chỉ cần 8 shard. `max` ở lượt này có **cap 2**, không phải thử mở đồng thời 11 hoặc 8 VM.

Trước mỗi lần tạo VM phải đặt reservation đủ 780 giây. Chỉ cấp VM khi tổng thời gian đã tiêu cộng các reservation đang giữ và 780 giây mới không vượt 1800 giây. Khi `terminate(wait=True)` xác nhận xong, thay reservation bằng elapsed từ trước create đến sau terminate, chặn trên ở lifetime 780 giây. Nếu trạng thái tạo/tắt VM không chắc chắn, giữ nguyên reservation và dừng cấp mới. Không tự retry trả phí.

Theo giá non-preemptible đã kiểm tra tại thời điểm chạy, đơn giá cấu hình này là `16×0.00003942 + 64×0.00000667 = 0.0010576 USD/giây`. 1800 giây tương đương **$1.90368 CPU/RAM**, dành phần còn lại của $2 cho egress nhỏ của log/output. Đây là admission guard phía client, **không phải hard spending cap do nhà cung cấp bảo đảm**; storage có hợp đồng riêng. Thực tế billing app không vượt $2.

Peak các khoảng VM trong receipt là **2**; thời gian có hai khoảng VM chồng nhau **163.453 giây**. Phần sau có lúc chỉ một VM vì reservation của hai lifetime không còn vừa trần. Vì vậy số đo không đại diện chạy cap 2 suốt lượt hoặc chạy toàn bộ shard cùng lúc.

Tổng khoảng thời gian coordinator gắn với VM là **472.891 giây**; gồm cả provisioning/SDK overhead, không đồng nhất với billed seconds. CPU/RAM bound từ các khoảng này là **$0.50012943**. Reservation cao nhất ghi được **1745.606/1800 giây**. Lịch sử đầy đủ trong receipt và archive.

Tối ưu chi phí tại tài nguyên cố định cần giảm **tổng thời gian VM**, không chỉ giảm thời điểm VM cuối cùng xong. Chạy đồng thời tự nó không loại bỏ startup, test hay cleanup; nó có thể đổi throughput/I/O và do đó thay đổi cả tổng thời gian. Load balancing chỉ hữu ích nếu giảm VM-time hoặc cho phép cấu hình tài nguyên khác đã được kiểm chứng riêng.

## Timer: test và cleanup có đo riêng được không?

| Shard | Bài | Bắt đầu* | Kết thúc* | Docker ready | Worker | Thu artifact | Terminate |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 4 | 0.726 | 77.758 | 13.756 | 59.651 | 0.547 | 1.348 |
| 2 | 2 | 0.727 | 46.389 | 13.725 | 29.943 | 0.082 | 1.617 |
| 4 | 1 | 46.393 | 164.194 | 8.545 | 105.537 | 0.523 | 1.410 |
| 6 | 6 | 77.763 | 140.681 | 9.779 | 49.587 | 0.542 | 1.286 |
| 7 | 3 | 140.686 | 192.337 | 14.573 | 35.075 | 0.089 | 1.587 |
| 8 | 4 | 192.344 | 234.919 | 7.869 | 31.004 | 0.605 | 1.386 |
| 9 | 3 | 234.926 | 274.833 | 11.001 | 25.219 | 0.533 | 1.381 |
| 10 | 1 | 274.840 | 310.197 | 3.212 | 28.602 | 0.460 | 1.301 |

Đơn vị giây; *offset so với lúc coordinator bắt đầu. Worker bao gồm kiểm tra store image, gọi harness, parse report, xóa image và kiểm tra container; không phải pure test. Copy/read-back source là phase riêng trong receipt. Một số SDK/app overhead nằm ngoài các phase trên.

Để giữ worker byte-identical, lượt này **không thêm timer bên trong test/cleanup**. Từ log 24 bài của lượt tuần tự có thể lấy `Test runtime` (eval script, không chỉ pytest) và thời gian từ “Attempting to stop container” đến “Container ... removed”. Median cleanup container lịch sử là khoảng 15.138 giây; cộng 24 khoảng là 363.463 giây **có chồng lấn**, không được trừ trực tiếp khỏi wall time. Timer xóa Docker image riêng vẫn chưa có. Không gọi phần dư của timer là pure cleanup.

## Thống kê đủ 500 bài và mức thiếu dữ liệu

Đã đọc metadata của **đủ 500 bài** từ dataset pin có sẵn trên GCP; không tải/build image mới để làm phân tích. Full dataset JSON SHA-256 `6a6fb72484bfc40f35cecbae1ae4615e9460905f729c57659df5f4ef9d7dec6e`. [task-features.json](task-features.json) chứa repo/version và số entry FAIL_TO_PASS, PASS_TO_PASS; không chứa runtime của 500 bài.

| Shard index | Số bài | Thành phần repo | Tổng test entry | Bài có timing | Unique layer tar GiB |
|---:|---:|---|---:|---:|---:|
| 0 | 100 | astropy/astropy: 22, django/django: 78 | 11428 | 4 | 29.909 |
| 1 | 100 | django/django: 100 | 9017 | 0 | 24.691 |
| 2 | 59 | django/django: 53, matplotlib/matplotlib: 6 | 5311 | 2 | 33.684 |
| 3 | 8 | matplotlib/matplotlib: 8 | 1503 | 0 | 32.778 |
| 4 | 9 | matplotlib/matplotlib: 9 | 6187 | 1 | 33.224 |
| 5 | 9 | matplotlib/matplotlib: 9 | 3329 | 0 | 29.620 |
| 6 | 27 | matplotlib/matplotlib: 2, mwaskom/seaborn: 2, pallets/flask: 1, psf/requests: 8, pydata/xarray: 14 | 10519 | 6 | 33.362 |
| 7 | 21 | pydata/xarray: 8, pylint-dev/pylint: 10, pytest-dev/pytest: 3 | 6086 | 3 | 34.866 |
| 8 | 43 | pytest-dev/pytest: 16, scikit-learn/scikit-learn: 27 | 2602 | 4 | 34.820 |
| 9 | 100 | scikit-learn/scikit-learn: 5, sphinx-doc/sphinx: 44, sympy/sympy: 51 | 4654 | 3 | 32.997 |
| 10 | 24 | sympy/sympy: 24 | 1016 | 1 | 7.639 |

Số bài shard lớn nhất/nhỏ nhất là 12.5×, CV khoảng 0.8008. **Điều này chưa chứng minh lệch runtime hay lãng phí tiền.** Các repo, version, test và layer dùng chung khác nhau; số test entry không được coi là thời gian chạy.

Chỉ **24 bài có log timing gold**, mỗi repo có 1–3 mẫu; **476 bài chưa có runtime đo**. Mô hình giữ timing thực của 24 bài, gán median theo repo cho 476 bài còn lại. Đây là proxy yếu, chịu selection bias và không dự đoán đáng tin cậy patch sai, timeout, long tail hoặc resource contention của full 500.

## N16(max): phân tích offline, chưa build hay chạy

Heuristic thử N=9,10,11,12,16,20: giữ tối đa 100 image và 35 GiB unique expanded layer-tar mỗi shard; seed N shard rồi giảm marginal tổng queue time của 8 worker (LPT), tie-break bằng bytes layer lặp. **35 GiB là envelope nội bộ đã dùng để chuẩn bị snapshot, không phải giới hạn Modal.** Không chứng minh tối ưu toàn cục. Các lần heuristic thất bại ở N9/N10/N11 không chứng minh bất khả thi; N11 hiện có rõ ràng đã tồn tại.

Unique expanded layer tar toàn dataset khoảng 284.487 GiB; tổng theo N11 hiện tại **327.589 GiB**. Candidate N16 là **415.327 GiB**, tăng **26.783%** do mất chia sẻ layer giữa các shard. Đây không phải số byte storage vật lý được Modal tính tiền; rootfs, COW, metadata, nén và dedup phía nền tảng chưa nằm trong mô hình.

Proxy tổng VM-time cho phần phục vụ task: N11 hiện tại **1857.015s**, N16 **1819.386s**. Phần này đã gồm task cleanup lấy từ log và queue 8 worker, chưa gồm toàn bộ startup/verification/transfer/artifact/tắt VM. Thử cộng overhead cố định mỗi VM:

| Overhead/VM giả định (s) | N11 proxy tổng VM-s | N16 proxy tổng VM-s | N16 thay đổi chi phí ở cùng resource |
|---:|---:|---:|---:|
| 5 | 1912.015 | 1899.386 | -0.661% |
| 15 | 2022.015 | 2059.386 | +1.848% |
| 30 | 2187.015 | 2299.386 | +5.138% |

N16 tiết kiệm proxy phần task 37.629 giây nhưng thêm 5 VM; điểm hòa vốn overhead là khoảng **7.526 giây/VM**, chưa tính chi phí build/kiểm chứng mới và lưu trữ. Đây là sensitivity analysis, không phải dự báo billing hoặc số đo full 500. Candidate N12 trong model cũng không kém N16 về proxy tổng thời gian, cho thấy không có lý do đặc biệt để mặc định N16.

**Khuyến nghị hiện tại: giữ catalog N11; giữ mode tuần tự đã đạt parity 24 bài làm reference, N11max ở trạng thái experimental/unqualified. Chưa đầu tư N16 chỉ để cân bằng số bài.** Nếu thử N khác, cần định nghĩa số lượt eval dự kiến để amortize build, thu timing đại diện theo repo/version và kiểm tra mục tiêu `build_cost / số_lượt + eval_cost + storage_cost`. Lượt N11 tuần tự trước đã match 24 gold control, còn lượt song song mới có một sai khác; chưa có đủ dữ liệu để chọn N tối ưu cho arbitrary full-500 predictions. Không suy rộng mức giảm chi phí pilot sang 500 bài.

## Mode được lưu và cách tái sử dụng

`python eval_modes.py list` có `native`, `optimizeN11`, `optimizeN11max`. Native giữ official --modal true cùng compatibility overlay cũ; không chạy lại native trong lần này. N11 giữ runner tuần tự nguyên bản. N11max là coordinator cap 2/$2 đang được kiểm chứng ở đây.

Đây là research launcher cho đúng frozen 24 bài; **web/launch.py production vẫn dùng OCI**. Chưa quảng bá mode này là backend full-500 hoặc backend upload predictions tùy ý. Lệnh prepare/run/collect và venv cần dùng được ghi ở README gốc. Không chỉnh signing config; commit mới dùng unsigned theo ủy quyền của người dùng.

## Bằng chứng, kiểm tra và giới hạn

- [comparison.json](comparison.json): kiểm tra paired reports, scientific config, timeline overlap, reservation và billing.
- [Log test Matplotlib nguyên bản (.gz)](matplotlib-20859-test-output.txt.gz) giữ byte gốc, kể cả whitespace do pytest in ra.
- [receipt.json](receipt.json), [plan.json](plan.json), [reports.json](reports.json), các billing JSON và [source-manifest.json](source-manifest.json).
- [raw-evidence.tar.gz](raw-evidence.tar.gz): input, per-shard source readback, report, harness/test log, assignment, output và receipt gốc; không chứa Docker image hoặc secrets.
- [cost-model.json](cost-model.json): toàn bộ 500 metadata, 24 timing lịch sử, proxy, candidate assignment refs và các giới hạn ước lượng. Không có snapshot N16 mới.
- [offline-probes.txt](offline-probes.txt): 71 probe tests pass; 50 core/web/cache tests pass trước launch; 17 tests preflight trên GCP pass cùng read-back 6 file. Mutation tests chặn thay CPU, timeline giả không có song song, vượt reservation, sai source và billing chưa ổn định. Test mới giữ nguyên report lệch và bắt buộc `qualified=false`.
- `SHA256SUMS.txt` kiểm tra byte các artifact; khi đọc từ Git, dùng blob LF chuẩn để tránh chuyển newline của Windows.

Không thể xác nhận tuyệt đối “không có sai sót” cho mọi task: lần này kiểm chứng resource/source/image giữ nguyên, nhưng **chỉ 23 report match và 1 report fail khác reference**. Còn thiếu phép chẩn đoán timing có kiểm soát, thử negative/empty/timeout trên backend snapshot mới và chạy arbitrary full-500; các phần đó chưa được tiến hành ở đây. Không xóa bằng chứng fail hoặc coi harness exit 0 của từng worker là qualification.

Nguồn chính thức đã kiểm tra ngày chạy: [Modal pricing](https://modal.com/pricing) cho CPU/RAM non-preemptible; [filesystem snapshot retention](https://modal.com/docs/guide/sandbox-snapshots) cho TTL. Giá Volume không được gán cho filesystem Image snapshot. Giá storage snapshot không nằm trong app billing được đối chiếu ở báo cáo này.
