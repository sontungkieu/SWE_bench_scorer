# Native Modal: bản vá tương thích và kết quả control

Ngày 2026-10-21, profile **phamvanvuhoan**. Backend `--modal true` của
**SWE-bench 4.1.0 + Modal SDK 1.6.2** đã chạy thành công với overlay tương
thích. Đây là qualification một instance `django__django-11099`, không phải
điểm mô hình hoặc qualification toàn bộ 500 bài.

| Control | Kết quả | Bằng chứng |
|---|---|---|
| Gold | Resolved, 0 lỗi hạ tầng | 3 FAIL_TO_PASS và 19 PASS_TO_PASS thành công |
| Wrong | Unresolved, 0 lỗi hạ tầng | 2 FAIL_TO_PASS thất bại; 19 PASS_TO_PASS thành công |
| Empty | Bỏ qua test, tính unresolved | CLI trả 0 và `No instances to run.`; không có official report |

Report JSON đầy đủ của gold và wrong **giống toàn bộ nội dung đã parse**
của report Docker control trước đây, gồm từng test status. Đây là đối chiếu
một bài, không suy rộng thành parity toàn benchmark.

## Thay đổi

[compat.patch.gz](compat.patch.gz) chỉ sửa `harness/modal_eval/run_evaluation_modal.py`
trong **bản sao package** của mỗi run:

1. Pin worker `swebench==4.1.0`, `modal==1.6.2`, `tenacity==9.2.1`, `ghapi==1.1.1`.
2. Thay `Sandbox.open(...).write(...)` bằng `Sandbox.filesystem.write_text(...)`.
3. Bỏ ghi `/sys/fs/cgroup/cpu/cpu.shares`; giữ yêu cầu `cpu=4` của Sandbox.
4. Gọi script entrypoint nguyên gốc bằng đường dẫn tuyệt đối, độc lập với cwd.
5. Terminate Sandbox trong `finally` sau khi chấm.

[modal_sdk_compat.py](../../probes/modal_sdk_compat.py) kiểm tra SHA-256 source
upstream đã audit trước khi vá; không nhận source lạ âm thầm. Manifest kiểm
tra toàn bộ bản sao package: đúng một file thay đổi. `TestSpec`, script tạo
test/environment, parser và grading giữ nguyên byte; các hash chính ở
[compat-manifest.json](compat-manifest.json). Package đã cài trên GCP được
đọc lại sau run và vẫn có hash nguyên bản. Không sửa dịch vụ web, backend
Docker, cache hay image đã triển khai.

API ghi file mới được mô tả trong [Modal filesystem docs](https://modal.com/docs/guide/sandbox-files).
Upstream được pin tại [SWE-bench v4.1.0](https://github.com/SWE-bench/SWE-bench/tree/v4.1.0).

## Hai lần thử trong lượt này

Lượt `upstream-modal-compat-20261021-01` chấm gold/wrong đúng kỳ vọng. Probe
ban đầu đánh fail empty vì đợi aggregate, trong khi upstream return sớm
khi toàn bộ patch rỗng. [Summary lần đầu](attempt-01-summary.json) được giữ
nguyên; đây là lỗi diễn giải của probe, không phải lỗi runtime hoặc test.

Probe đã sửa hạch toán empty: chỉ chấp nhận khi fixture đã validate thực sự
rỗng, CLI exit 0, không timeout, có dòng `No instances to run.`, không có
traceback. Nó ghi `aggregate_origin=probe empty-patch accounting` và
`official_report_written=false`; không tạo report chính thức giả.

Lượt chạy lại `upstream-modal-compat-20261021-02` đạt **3/3 control**,
probe exit **0**. Gold mất **20.765372421592474 giây**, wrong
**26.64343381114304 giây**, empty **6.192132700234652 giây**. Các thời gian
gồm khởi động CLI/app và dùng image cache đã có; không phải benchmark
so sánh tốc độ với Docker hoặc dự báo chi phí cho 500 bài.

Năm regression test offline pass, kiểm tra cả exit 0 nhưng error, thiếu
report, kết quả gold/wrong, empty trong aggregate và empty return sớm.

```powershell
python -B -m unittest -v probes.test_upstream_modal_probe
```

## Billing, cleanup và bằng chứng

Cả bốn app của hai lượt đều **stopped**, tasks **0**. SDK không thấy Sandbox
còn lại thuộc từng app; link credential tạm đã được dọn. Không stop app
của workflow cache đang chạy song song. [Cleanup](cleanup.json).

Billing snapshot lúc **19:53:56 giờ Việt Nam** ghi tổng riêng bốn app
**$0.00537799 trước credits**, chưa phải hóa đơn cuối. Khoản này bao gồm
cả lần thử đầu và chạy lại; tách khỏi **$0.01946276** của phép thử upstream
nguyên gốc ở lượt trước. Dự trù guard $0.50 không phải provider hard cap.
[Billing theo app/resource](billing.json).

[verification.json](verification.json) và [summary.json](summary.json) ghi
kết quả, hash source đã thực thi và đối chiếu Docker. `gold-report.json`,
`wrong-report.json` là official instance report; `*-official-aggregate.json`
là official aggregate. `empty-accounting.json` là hạch toán riêng của probe.
`*-console.log.gz` giữ log đầy đủ; SHA256SUMS kiểm tra mọi file bằng chứng.
Full archive của cả hai lượt giữ local tại
`runs/upstream-modal-compat-20261021-02/compat-evidence.tar.gz`, không chứa
dataset hoặc credential. Dependency được cài riêng trên GCP; laptop chỉ
dùng toolchain có sẵn để sửa, kiểm tra nhẹ và lấy log.

## Chạy lại

Trên coordinator GCP đã có venv riêng và scoped config, sau billing guard:

```bash
MODAL_CONFIG_PATH="$HOME/.config/swebench-web/modal.toml" \
MODAL_PROFILE=phamvanvuhoan PYTHONUNBUFFERED=1 \
venv/bin/python probe.py --compat --fixtures mock --output native-modal-NEW-ID
```

Copy cả [probe.py](../../probes/upstream_modal_probe.py) và
[modal_sdk_compat.py](../../probes/modal_sdk_compat.py) tới cùng thư mục.
Mỗi run tạo overlay riêng; không sửa `site-packages` dùng chung. Không có
`--compat` thì probe vẫn gọi upstream nguyên bản.

Native Modal hiện là phương án khả thi cho smoke này. Chưa thay backend
production, chưa kiểm tra file prediction VAI/full 500 bài, chưa kiểm tra
mọi repository/dependency và chưa có lock đầy đủ cho dependency phụ.
