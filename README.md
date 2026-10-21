# Chấm SWE-bench Verified trên Modal

Phiên bản: **0.2.0**.

## Web riêng tư trên GCP

Upload **JSON, JSONL hoặc CSV**, validate, chọn smoke/full hoặc chạy mock,
xem lịch sử/live log và tải archive kết quả. Web nhẹ chạy trên GCP; official
harness và Docker chạy trên Modal. [Hướng dẫn truy cập và vận hành](deploy/README.md).

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:18765:127.0.0.1:8765 Free-tierGCP
# Giữ terminal chạy, rồi mở http://127.0.0.1:18765
```

CLI cũng đọc `.json`, `.jsonl`, `.csv` qua `--predictions`; ba trường bắt buộc
là `instance_id`, `model_name_or_path`, `model_patch`. JSON nhận list hoặc mapping
theo ID. CSV cần header đúng và quote patch chứa dấu phẩy/xuống dòng. Browser
upload file; không dùng đường dẫn trên máy cá nhân để yêu cầu server đọc file.

Web dùng SQLite queue bền vững, một job chạy mỗi lần, billing guard trước
launch và 127.0.0.1 listener sau SSH. Full 500 cần lựa chọn và xác nhận riêng;
chưa được chạy trong quá trình setup này. Không tự chạy lại job trả phí sau lỗi.

## CLI và bằng chứng trước đây

Đã chạy mock thật trên Modal profile `phamvanvuhoan`: **3/3 control pass**
(gold resolved, wrong unresolved, empty unresolved); 7/7 test offline v0.1.1 pass.
Xem [bằng chứng kiểm thử](evidence/2026-10-21-v0.1.0/verification.json)
và [release v0.1.1](https://github.com/sontungkieu/SWE_bench_scorer/releases/tag/v0.1.1).
Chưa chạy file prediction VAI hoặc full 500 bài.
Mock cloud được chạy ở v0.1.0; v0.1.1 chỉ sửa chính sách LF/checksum và version,
giữ nguyên thuật toán chấm. Receipt/log gốc v0.1.0 được giữ nguyên.

Nhận `preds.jsonl` theo trang 21 tài liệu VAI và chạy **official SWE-bench
harness 4.1.0** trong **Modal VM CPU**. Docker, dataset và image instance chỉ
tải/chạy trên Modal. Máy Windows dùng Modal client 1.6.2 đã có trong WSL.
Không cần vLLM, checkpoint hay GPU để chấm patch.

## Chạy từ PowerShell

```powershell
cd D:\dev\codex\research_vdt\swebench-modal

# Kiểm tra JSONL offline, không tạo tài nguyên cloud.
.\run.ps1 validate --predictions D:\path\to\preds.jsonl

# Sau khi refresh billing cho profile được phép dùng:
.\run.ps1 mock --profile phamvanvuhoan --run-id my-mock-001

# Chấm thử file thật; chọn ID thực sự có trong file.
.\run.ps1 smoke --profile phamvanvuhoan --predictions D:\path\to\preds.jsonl --instance-ids django__django-11099 --run-id vai-smoke-01

# Chỉ chạy khi đã được cấp ngân sách cho đủ 500 bài.
.\run.ps1 full --profile phamvanvuhoan --predictions D:\path\to\preds.jsonl --workers 8 --lifetime 86400 --estimate-usd 20 --run-id vai-full-01
```

Tên profile trong ví dụ là lựa chọn minh họa; không tự đổi profile đang active.
`--estimate-usd` là dự trù đưa vào billing guard, **không phải hard cap của Modal**
hay báo giá đã đo. Mock dùng 1 worker, 2 physical CPU cores, 8 GiB RAM,
lifetime tối đa 1800 giây. Full 8 workers yêu cầu 16 cores/64 GiB, cần quyền và
ngân sách riêng; ví dụ $20 chỉ là giá trị guard minh họa. Nếu bị guard chặn,
không bỏ guard hay đổi cấu hình ngân sách để vượt chặn.

Refresh billing trước launch, qua WSL và dependency có sẵn:

```bash
export PATH=/home/tung/.local/bin:$PATH
export UV_OFFLINE=1
python3 /mnt/c/Users/Tung/.codex/skills/modal-gpu-ops/scripts/modal_billing_guard.py snapshot --profile PROFILE
```

## Mock tạo gì?

Ba file `preds.jsonl` riêng dùng cùng bài thật `django__django-11099`:

1. `gold`: patch ground truth công khai, kỳ vọng resolved.
2. `wrong`: diff hợp lệ chỉ thêm file vô hại, giữ nguyên bug, kỳ vọng unresolved.
3. `empty`: patch rỗng, kỳ vọng unresolved (official harness có thể bỏ chạy test).

File mẫu đã có trong `fixtures/mock/{gold,wrong,empty}/preds.jsonl` cùng
`fixtures/mock/manifest.json` (checksum và kỳ vọng; chưa phải kết quả chấm).
Mock cloud cũng tạo input trong `runs/RUN_ID/output/{gold,wrong,empty}/preds.jsonl`.
Gold là **positive control có chủ đích**, không phải model prediction;
không gộp vào đầu vào VAI hay báo như điểm mô hình. File `mock-checks.json`
chỉ pass khi cả ba nhánh cho kết quả đúng và harness không lỗi.

## Đọc kết quả

`runs/console-*.log` lưu stdout/stderr của launcher. `runs/RUN_ID/receipt.json`:
profile, app/sandbox/image ID, source SHA-256,
thời gian, exit code, checksum archive và xác nhận terminate sandbox.
`results.tar.gz` cùng bản đã giải nén chứa:

- `environment.json`, `pip-freeze.txt`, `harness-help.txt`, dataset revision/hash.
- Mỗi case: `summary.json`, `harness.log`, image IDs/digests, prediction input,
  và nguyên bản logs/report của official harness dưới `logs/`.
- Nếu worker lỗi: `error.txt` và log còn thu được, không tạo điểm giả.

Smoke chỉ có `subset_resolved_rate`; `verified_score=null`. Full đòi đúng đủ
500 ID của dataset đã pin và tính `resolved / 500`, bao gồm patch rỗng.
Thiếu report cho patch không rỗng là lỗi hạ tầng/incomplete; score để `null`.
Harness exit code 0 một mình chưa đủ chứng minh pass; phải kiểm tra summary.
Full chạy từng batch tối đa `workers` image và xóa image batch đã chấm để
không tải trước toàn bộ 500 image vào disk VM. Toàn bộ 500 bài chưa được
qualification trong lần setup mock này.

## Runtime và khả năng tái lập

Dataset `SWE-bench/SWE-bench_Verified` test revision
`78f471bf655a3137b2e8a75af1501690ec009ec3` (500 bài). Harness pin 4.1.0,
client pin 1.6.2. Các dependency phụ được lưu `pip-freeze.txt`, runtime được
nhận diện bằng Modal image ID. Image test chính thức `swebench/sweb.eval...`
được pull từ Docker Hub rồi ghi content digest. Tag `latest` được resolve lại
ở mỗi VM; đối chiếu `images.json` trước khi so sánh hai lần chấm. Runtime apt
và dependency phụ chưa có lock hoàn chỉnh; Modal cache giữ image đã build,
không cam kết rebuild bit-for-bit chỉ từ source. Dữ liệu lớn không qua laptop.

Trong VM chạy `python -m swebench.harness.run_evaluation --modal false` vì
Docker đã ở trên Modal. Đây là official Docker harness trên Modal VM, không
phải backend `--modal true` tích hợp của SWE-bench. Lựa chọn này tránh backend
Modal mới nhất có TODO về TestSpec/setup scripts, đồng thời giữ nguyên test
và grading chính thức. Không sửa harness hoặc test benchmark.

Sandbox luôn terminate trong `finally`, có lifetime hữu hạn nếu client mất
kết nối. Image build/cache có billing riêng; không deploy dịch vụ chạy thường
trực, không mount hay chuyển credential vào VM. Outputs tải về trước terminate;
nếu VM hết lifetime trước khi tải thì một số log có thể không thu được.

Kiểm tra nhẹ offline bằng `python -m unittest -v test_core.py test_web.py`. Các unit test
chỉ kiểm tra JSONL và phép tính điểm; mock cloud mới xác nhận Docker/test runtime.

Nguồn: [SWE-bench evaluation](https://www.swebench.com/SWE-bench/guides/evaluation/),
[harness v4.1.0](https://github.com/SWE-bench/SWE-bench/tree/v4.1.0),
[Modal VM và Docker](https://modal.com/docs/guide/sandbox).
