# Chấm SWE-bench Verified trên Modal

Phiên bản: **0.3.1**.

Đã chấm lại **đúng 24 gold controls trên bộ snapshot mới**: 24/24 report khớp
baseline; **$0.50332977**, **515.25 giây**, dùng 8/11 snapshot chứa các bài đó.
[Báo cáo và giới hạn so sánh](evidence/2026-10-22-sharded-snapshot-pilot/README.md).
Chưa chấm full 500; mức giảm chi phí đo được so với OCI baseline là 49.14%.

[Báo cáo audit khoa học/kỹ thuật ngày 22/10/2026](evidence/2026-10-22-scientific-audit/README.md)
đối chiếu input, report parity, coverage từng snapshot và billing; ghi rõ giới
hạn suy rộng và fix validator mapping shard. Lệnh kiểm tra offline đi kèm.

Đã lưu và kiểm tra **11 snapshot VM bao phủ đủ 500 image**, TTL **90 ngày**:
[catalog và bằng chứng](evidence/2026-10-22-verified-snapshots-90d/README.md),
[quy trình và giới hạn](probes/SNAPSHOT_CATALOG.md). Đây là bước nạp môi trường;
chưa chấm full 500 và chưa đổi backend của web/CLI.

## Dùng Docker Hub token qua Modal Secret

Cache CLI nhận `--registry-secret swebench-dockerhub`. Secret cần hai key
`REGISTRY_USERNAME`, `REGISTRY_PASSWORD`; chỉ cache-only VM được nhận secret,
không đưa PAT vào VM chấm patch. Registry auth thất bại thì báo lỗi, không
fallback âm thầm về anonymous. Basic auth chỉ gửi tới `auth.docker.io`; bearer
không được chuyển sang CDN khi redirect. Không truyền PAT bằng flags hoặc log.

Từ WSL, dùng Modal runtime đã có để kiểm tra file và tạo secret (không tạo VM):

```bash
UV_OFFLINE=1 /home/tung/.local/bin/uv tool run --offline --from modal python \
  /mnt/d/dev/codex/research_vdt/swebench-modal/bootstrap_registry_secret.py \
  --profile phamvanvuhoan --credential-file /home/tung/dockerhub.txt \
  --secret-name swebench-dockerhub
```

File có hai dòng: username rồi PAT, giữ ngoài Git. Nếu file mới chỉ có PAT,
thêm `--username-file /home/tung/dockerhub.txt`: chỉ đọc dòng username đầu tiên
của file cũ. Windows Downloads dùng đường dẫn WSL `/mnt/c/Users/Tung/Downloads/…`.
Bootstrap xác thực và kiểm tra pull scope trước khi tạo secret, từ chối ghi đè
secret đã có. Docker Hub có thể dùng opaque account ID trong JWT `sub`.
Token mới đã xác thực và tạo secret đích ngày 2026-10-21. Sau đó:

```powershell
.\run.ps1 cache --all-instances --profile phamvanvuhoan --registry-secret swebench-dockerhub --lifetime 13500 --estimate-usd 1.60 --run-id cache-auth-01
```

Secret cũ `dpca-dockerhub-kieusontung8` tồn tại, nhưng probe/migration đã bị
provider chặn do workspace nguồn vượt spend limit, trước khi tạo Sandbox.
Chưa xác nhận key trong secret cũ còn hợp lệ; file credential gốc trả HTTP 401.
Không tự đổi spend limit hoặc copy credential không hợp lệ sang workspace đích.
`migrate_registry_secret.py` là phương án chuyển mã hóa khi nguồn được mở lại;
chưa được qualification live vì provider chặn allocation. Nó yêu cầu ngoại lệ
explicit nếu billing guard nguồn báo BLOCK, giới hạn probe 60 giây, dự trù $0.01.

## Cache image giữa các lần chấm

Web và CLI mặc định dùng Modal **Volume v2** `swebench-verified-images-v1`.
Lần đầu, các layer nén được tải trực tiếp từ Docker Registry API theo SHA-256;
không chạy Docker, giải nén ra disk hoặc nén lại trong lúc prewarm. Layer chung
chỉ lưu một bản. Manifest ghi image ID và registry digest cho linux/amd64.
Lần sau `docker load` trực tiếp từ Volume, kiểm tra hash và image ID; không
resolve lại tag khi đã có cache. Không mount Volume làm Docker overlay2 data root.
Docker vẫn chạy trên disk VM, batch image vẫn được dọn sau khi chấm full.

Đã kiểm chứng trên **hai VM độc lập**: nạp một image rồi mock với
`--require-cached` (cấm pull nếu thiếu cache), cả gold/wrong/empty đúng kỳ vọng.
Cache Docker-save thử nghiệm đầu tiên khôi phục khoảng 36 giây. Bản OCI tối ưu
nạp cùng image trong khoảng 26 giây (bản đầu khoảng 61 giây gồm pull/export),
khôi phục từ Volume trên VM mới khoảng 28 giây và mock pass 3/3 control.
Đây là số đo riêng một image, không cùng trạng thái cache/network. Đây chưa phải
benchmark tốc độ cho 500 bài. Patch rỗng không pull/load image hoặc chạy harness,
vẫn được tính unresolved trong mẫu số 500. Test chính thức không thay đổi.

```powershell
# Chỉ nạp image, không chạy test; mặc định một bài mock.
.\run.ps1 cache --profile phamvanvuhoan --run-id cache-one

# Nạp cả 500; lặp lại với run-id mới để tiếp tục, bài có manifest được bỏ qua.
# 2 cores / 2 GiB, tối đa 13.500 giây; dự trù cho guard $1.60.
.\run.ps1 cache --all-instances --profile phamvanvuhoan --lifetime 13500 --estimate-usd 1.60 --run-id cache-all-01

# Kiểm chứng chỉ dùng image đã lưu, không fallback về registry.
.\run.ps1 mock --profile phamvanvuhoan --require-cached --run-id cache-check
```

Giới hạn mặc định **256 GiB blob nén**, đổi bằng `--cache-limit-gib`.
Hết dung lượng, thời hạn hoặc gặp lỗi registry thì dừng; các manifest đã commit
vẫn dùng được. `output/cache-summary.json` ghi số đã cache, lỗi và dung lượng.
Nạp xong image không có nghĩa là đã eval hay qualification đủ 500 bài.
Để tắt cache dùng `--cache-volume none`; khi đó mỗi VM mới có thể phải pull lại.
Cache hỏng/mất blob hoặc image khác manifest sẽ báo lỗi, không âm thầm đổi bản.

Chỉ chạy một cache writer mỗi lần. Trên GCP, launcher và web dùng cùng file lock
trong state: job đến sau chờ trước khi tạo VM, không tính compute khi chờ.
Không nạp từ client khác vào cùng Volume trong lúc GCP đang ghi cache.
`sync /image-cache` commit blob trước rồi mới publish/commit manifest, theo
[cơ chế Volume v2](https://modal.com/docs/guide/volumes#in-v2-you-can-commit-using-sync).

Theo [giá Modal kiểm tra ngày 2026-10-21](https://modal.com/pricing), cache-only
2 cores/2 GiB có allocation CPU/RAM tối đa $0.331848/giờ, tương ứng $1.24443
cho 13.500 giây. Launcher từ chối thời hạn có bound CPU/RAM vượt estimate của
cache job. Đây không phải provider hard cap hay bao gồm mọi phí khác. Volumes
có 1 TiB miễn phí/tháng dùng chung workspace; chưa xác nhận tổng dung lượng
Volume của mọi dự án. Thời gian test vẫn tính compute, cache không xóa khoản đó.

Lượt nạp ngày 2026-10-21 đã commit **101/500 image**, khoảng **17 GiB** gồm
prototype cũ, rồi dừng ở HTTP 429 của Docker Hub; VM đã terminate. SDK đọc lại
xác nhận có 101 OCI manifest sau khi VM dừng. Lượt bulk mất khoảng 519 giây,
chi phí CPU/RAM/egress được báo cáo $0.04905196; tổng cả các lượt thử cache và
mock $0.10410261 trước credits, chưa phải hóa đơn cuối hoặc phí storage chung.
[Receipt, billing và tiến độ](evidence/2026-10-21-v0.3.0/verification.json).

Khi không có `--registry-secret`, downloader dùng anonymous của Registry API. Theo
[Docker Hub](https://docs.docker.com/docker-hub/usage/pulls/), anonymous có
100 pull/6 giờ/IP; Personal đăng nhập có 200/6 giờ; Pro/Team/Business không có
giới hạn pull đó, vẫn chịu fair use. Token Personal không tự nâng thành Pro.
`docker login` trên GCP không tự truyền vào downloader trong Modal; kết nối
token cần kênh secret riêng. Không đưa PAT/token vào source, flags hoặc log.
Trạng thái credential và các lượt nạp mới hơn được ghi riêng trong evidence.

### Nạp tiếp hữu hạn, chờ quota trên GCP

`prewarm_queue.py` dùng runtime Modal có sẵn trên GCP, không đọc PAT. Mỗi VM có
lifetime hữu hạn; chỉ tạo VM tiếp khi receipt xác nhận VM trước đã terminate.
Hết thời lượng sạch thì nối tiếp; HTTP 429 thì chờ trên GCP. Lỗi auth/khác hoặc
receipt không rõ thì dừng. Manifest đã commit được bỏ qua khi chạy tiếp.

```bash
# Chạy trong source release; dùng service.env riêng, không đưa vào source.
set -a; . ~/.config/swebench-web/service.env; set +a
~/apps/swebench-web/runtime/venv/bin/python prewarm_queue.py \
  --profile phamvanvuhoan --registry-secret swebench-dockerhub \
  --state ~/apps/swebench-web/state --run-prefix cache-next-unique \
  --budget-usd 2 --prior-spend-usd 0.11 --egress-reserve-usd 0.05 \
  --lifetime 1800 --max-attempts 3
```

`--adopt-run ID` theo dõi một lượt đã chạy, rồi mới nối tiếp. Không tái sử dụng
run prefix; file lock chặn hai coordinator. Budget cộng chi phí cũ, dự phòng và
CPU/RAM tối đa theo lifetime trước mỗi allocation; không phải provider hard cap.
1800 giây có bound $0.165924/VM. Lượt hiện tại + tối đa ba lượt nối tiếp, cộng
$0.11 cũ và $0.05 dự phòng, có bound $0.823696 theo các giả định tài nguyên này.

Để chuyển account, xác thực credential và tạo Secret mới trước, rồi adopt lượt
đang chạy với tên `--registry-secret` mới. Coordinator chờ VM cũ terminate,
bỏ qua cooldown quota cũ đúng một lần khi tên Secret khác receipt đã adopt.
Nếu credential mới cũng gặp HTTP 429 thì chờ cooldown như thường. Khi chuyển
coordinator phải cộng mọi allocation cũ vào prior budget, tránh đặt lại ngân sách.

Mặc định cooldown 6 giờ + 60 giây. Header xác thực quan sát ngày 2026-10-21 là
`200;w=3600`; lượt này dùng `--cooldown-seconds 3660` theo header thực tế đó,
không coi đây là chính sách chung thay cho docs Docker Hub. File state
`image-cache-status.json` lưu tiến độ; web `/api/cache` chỉ trả count, size,
status, thời điểm cập nhật/chạy tiếp. Không mở quyền chấm full tự động.

## Web riêng tư trên GCP

Upload **JSON, JSONL hoặc CSV**, validate, chọn smoke/full hoặc chạy mock,
xem lịch sử/live log và tải archive kết quả. Web nhẹ chạy trên GCP; official
harness và Docker chạy trên Modal. [Hướng dẫn truy cập và vận hành](deploy/README.md).

Đã kiểm chứng v0.2.0: **19/19 test** trên Windows và GCP, **3/3 mock control**
tạo qua web đúng kỳ vọng, **CSV upload chấm thật resolved** trên một gold patch.
[Receipt và kiểm chứng](evidence/2026-10-21-v0.2.0/verification.json).
Visual/browser QA chưa xác nhận do công cụ automation không khởi tạo được;
API, HTTP GET và download/checksum đã kiểm tra. Chưa chạy full 500 bài.

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
được pull từ Docker Hub rồi ghi content digest. Cache đã có được pin theo
manifest; chỉ cache miss hoặc tắt cache mới resolve tag `latest`.
Đối chiếu `images.json` trước khi so sánh hai lần chấm. Runtime apt
và dependency phụ chưa có lock hoàn chỉnh; Modal cache giữ image đã build,
không cam kết rebuild bit-for-bit chỉ từ source. Dữ liệu lớn không qua laptop.

Trong VM chạy `python -m swebench.harness.run_evaluation --modal false` vì
Docker đã ở trên Modal. Đây là official Docker harness trên Modal VM, không
phải backend `--modal true` tích hợp của SWE-bench. Backend Docker đang dùng
giữ nguyên harness, test và grading chính thức. TODO TestSpec của upstream
mới nhất không áp dụng trực tiếp cho phiên bản 4.1.0 đã pin.

Đã đo riêng hai đường trên cùng 24 gold patch, 12 repo: VM nhận đủ 24 report,
24 gold resolved, chi phí được ghi nhận $0.98970092; native 4.1.0 + SDK
compatibility overlay nhận 22 report, 20 gold resolved, chi phí $0.46610054.
Các số trước credits, không phải điểm model hoặc chi phí full 500 bài.
Hai app đã dừng; chưa chuyển backend production sang native.
[Protocol, sai khác từng bài và billing](evidence/2026-10-22-cost-pilot/README.md).

Thử nghiệm tiếp theo giữ nguyên 24 gold patch và tài nguyên VM chấm nhưng
khởi tạo từ filesystem snapshot đã nạp sẵn Docker image: 24/24 report khớp
baseline, compute/egress mỗi lượt $0.17555287 (giảm 82,26%), setup một lần
gồm canary $0.10441159. Snapshot chỉ chứa 24 image của pilot, chưa tích hợp
vào web/CLI production và chưa chạy full 500.
[Kết quả, snapshot ID và billing](evidence/2026-10-22-vm-snapshot/README.md).

Sandbox luôn terminate trong `finally`, có lifetime hữu hạn nếu client mất
kết nối. CPU/RAM trong lúc build, pull và load image đều tính compute; Volume
có hạn mức storage riêng. Không deploy dịch vụ chấm chạy thường trực, không
chuyển credential vào VM. Outputs tải về trước terminate;
nếu VM hết lifetime trước khi tải thì một số log có thể không thu được.

Kiểm tra nhẹ offline bằng `python -m unittest -v test_core.py test_web.py test_image_cache.py test_registry_cache.py test_prewarm_queue.py`.
Unit test kiểm tra input/score, queue/web và cache roundtrip, dedup, corruption,
partial writes, storage limit, empty-patch skipping; mock cloud xác nhận Docker/test runtime.

Nguồn: [SWE-bench evaluation](https://www.swebench.com/SWE-bench/guides/evaluation/),
[harness v4.1.0](https://github.com/SWE-bench/SWE-bench/tree/v4.1.0),
[Modal VM và Docker](https://modal.com/docs/guide/sandbox).

## Các mode so sánh snapshot (thực nghiệm, đúng 24 gold control)

`python eval_modes.py list` liệt kê các đường chạy và mode lập phương án: `native` dùng
official `--modal true` với SDK compatibility overlay; `optimizeN11` giữ
nguyên runner snapshot tuần tự; `optimizeN11max` chạy tối đa **2 VM** đồng thời.
**N=11 là tổng shard của catalog 500 image**, workers trong mỗi VM vẫn là 8.
Pilot chỉ dùng 8/11 shard. Cả hai mode snapshot giữ 16 cores/64 GiB mỗi VM,
test timeout 600 giây và cùng frozen input/source/image; mỗi VM tắt ngay sau
khi thu output. Native cần venv đã có SWE-bench 4.1.0, có protocol tài nguyên
khác và chưa đạt report parity trong lần đo trước.

Đây là entry point nghiên cứu riêng; web và `launch.py` production vẫn dùng
OCI backend hiện có. Chưa mở lệnh chấm full 500 qua các mode snapshot.
Chuẩn bị thư mục mới từ một reference run đã đủ input (không ghi đè run cũ):

```bash
python eval_modes.py prepare --out NEW_RUN --reference FROZEN_SHARDED_RUN --commit SOURCE_COMMIT
python eval_modes.py run --mode optimizeN11max --out NEW_RUN --source QUALIFIED_SOURCE --baseline ORIGINAL_COST_PILOT
python eval_modes.py collect --mode optimizeN11max --out NEW_RUN --billing-name billing-first.json
```

Chạy trong GCP runtime đã có Modal 1.6.2, scoped env của dịch vụ và
`MODAL_PROFILE=phamvanvuhoan`; không cài dependency hoặc build Docker local.
Mode N11max hiện khóa cap 2 và trần $2: trước mỗi VM phải dự phòng đủ lifetime
780 giây, tổng reservation + thời gian VM đã xong không vượt 1800 giây
($1.90368 CPU/RAM, còn phần nhỏ cho egress). Không giảm CPU/RAM hay timeout để
chạy vừa ngân sách. Nếu không còn đủ reservation, dừng với kết quả incomplete;
không tự retry. Billing thực tế vẫn cần đọc sau run, trước credits và không
bao gồm giá lưu snapshot. Chạy song song giảm wall time nhưng không tự động
giảm tổng VM-seconds/chi phí.

Lượt N11max cap 2 ngày 22/10/2026 đã thu đủ 24 report nhưng chỉ **23 report
khớp** với N11 tuần tự: `matplotlib-20859` không phát warning mà một test
PASS_TO_PASS yêu cầu. Test upstream này phụ thuộc ngưỡng thời gian 1 giây.
Giữ kết quả chính thức, **chưa qualification N11max**, không bỏ test hay sửa
score để làm parity pass. [Billing, chẩn đoán và phân tích N16](evidence/2026-10-22-optimizeN11max/README.md).

`probes/matplotlib_timing_probe.py` là phép chẩn đoán riêng, một VM 16 cores/
64 GiB: chạy cố định hai lượt harness trên nhóm shard 2 gốc, rồi hai lượt
quan sát bằng pytest plugin. Plugin dùng `sys.setprofile` để ghi khoảng thời
gian bao quanh clock sample thật tại điều kiện warning và phân biệt early
return; không monkeypatch clock, sửa test hoặc thay official report. Profiler
có overhead, nên bằng chứng này được lưu riêng và không thay kết quả 24 bài.
Đã đo trên hai VM có CPU model khác nhau ở cùng resource request; 8 clock
samples quan sát đều trên 1 giây và test pass. Chưa tái hiện nhánh dưới ngưỡng
trong loạt chẩn đoán, nên giả thuyết tốc độ cho lượt fail cũ vẫn là suy luận
có căn cứ, chưa phải clock lịch sử đã đo. [Báo cáo đầy đủ, tất cả repetitions
và kiểm tra parser](evidence/2026-10-22-matplotlib-timing/README.md).

### Mode gộp N7 riêng, giữ nguyên N11

`optimizeN7merge` lưu đúng cách gộp đề xuất: `100,100,59,(27+21),43,100,(24+8+9+9)`.
N=7, không phải 6. Năm snapshot có thể tái dùng sau kiểm tra live; hai nhóm gộp
cần build snapshot mới từ OCI cache đã pin. Nhóm lớn nhất là **95,364 GiB layer tar**.
Không thể ghép trực tiếp các ID snapshot hoặc thư mục Docker store để có snapshot mới.

`optimizeN7weighted` tìm cách gộp **nguyên các shard N11** thành 7 nhóm, giới hạn
64 GiB layer tar/100 image mỗi nhóm, tối thiểu hóa tổng thời gian hàng đợi LPT
8 worker theo trọng số. Đây là proxy từ 24 thời gian gold đã đo và 476 giá trị
điền bằng median từng repo, chưa phải dự báo billing. Không đổi thứ tự test
thật theo LPT. Kết quả hiện tại có nhóm lớn nhất 63,954 GiB, cần 4 snapshot mới.

Hai catalog đã build/restore thành công và được chấm đúng 24 controls:
N7merge đạt **23/24**, N7weighted **22/24**, nên **chưa qualified report parity**.
`eval_modes.py` vẫn chỉ cho N7 lập phương án offline; `prepare`, `run`, `collect`
bị chặn rõ ràng, không rơi vào runner N11 cũ. Tất cả mode N11/catalog pin và
backend web giữ nguyên. Lệnh lập phương án không dùng
Modal, không cần credential, không tạo VM:

```bash
python eval_modes.py plan --mode optimizeN7merge --out NEW_N7_MERGE_PLAN
python eval_modes.py plan --mode optimizeN7weighted --out NEW_N7_WEIGHTED_PLAN
```

Mỗi thư mục mới chứa `merge-plan.json.gz`: toàn bộ assignment, OCI identity,
hash nguồn, dung lượng layer đã khử trùng và giới hạn suy luận. Không ghi đè
plan/run cũ. Catalog mới đã read-back và runner riêng đã chấm đủ 24 bài với
lifetime/billing guard. Nhóm **9 bài** ở N7merge đã chạy bằng hàng đợi tối đa
8 worker, không bỏ bài hoặc tăng concurrency. Snapshot được giữ lại; trước
khi mở mode chạy tiếp cần qualification phù hợp cho các required-test mismatch,
không tự build lại hay retry để lấy pass. Chưa mở full 500.
[So sánh hai phương án và các bước còn lại](evidence/2026-10-22-merged-snapshot-plans/README.md).
[Kết quả build/test, failure, chi phí và log đầy đủ](evidence/2026-10-22-n7-merged-qualification/README.md).

`probes/merged_snapshot_experiment.py` là coordinator riêng cho lần qualification
cả hai cách gộp, chưa thay dispatch/catalog pin của N11. `prepare --reuse-bases` chỉ đóng băng
input/manifest; `run` có trả phí và chỉ dùng sau khi được duyệt ngân sách mới.
Hợp đồng: tối đa 6 build (4 core/4 GiB) + 6 restore verify (2 core/4 GiB), hai lượt đúng 24
controls (16 core/64 GiB, 8 worker, timeout từng bài 600s), ngân sách compute/egress
đã nâng lên $8 tính cả lượt đầu dừng. Mỗi lượt score có deadline 3000s; trước
mỗi VM phải còn đủ lifetime cho toàn bộ timeout waves. Queue 9 bài cần 1380s
dự phòng, không tăng worker hoặc giảm timeout. Thiếu dự phòng thì dừng incomplete.
Không tự retry, không chấm full 500. Snapshot mới TTL 90 ngày; storage được
theo dõi riêng, không nằm trong envelope compute/egress một lần này.

```bash
python probes/merged_snapshot_experiment.py prepare --reuse-bases --prior-reservation 0.19 --out NEW_DUAL_RUN --source SOURCE --reference QUALIFIED_N11_RUN --commit SOURCE_COMMIT
python probes/merged_snapshot_experiment.py run --out NEW_DUAL_RUN --source SOURCE
python probes/merged_snapshot_experiment.py collect --out NEW_DUAL_RUN --billing-name billing-first.json
```

Build và từng mode score có app billing riêng, VM chạy tuần tự và tắt sau thu
artifact. Worker N7 dùng hàng đợi tới 24 controls, không sửa worker N11/evaluator
gốc. Thu Docker events, CPU model và guest counters trước/sau ngoài test process;
không patch clock hay profile test. Các số này hỗ trợ phân tích setup/test/cleanup,
không đo trực tiếp provider physical I/O. `receipt.json` giữ mọi kết quả và mismatch.

Recovery build được duyệt riêng có thể `prepare --reuse-bases --prior-reservation 0.19`:
khởi động từ snapshot N11 sạch có lượng layer lớn nhất trong nhóm, xác nhận
manifest/OCI IDs của base rồi chỉ nạp image còn thiếu qua loader cũ. Builder
dùng 4 core/4 GiB và lifetime 1100s; restore verify vẫn 2 core/4 GiB, 120s.
Envelope mới $7.6383504 CPU/RAM; cộng $0.19 dự phòng cho tối đa hai builder cũ
là $7.8283504, còn phần nhỏ cho egress trong trần tổng $8. VM chấm vẫn 16 core/
64 GiB, 8 worker, timeout 600s. Không thay code/test benchmark để giảm thời gian.

### Độ ổn định và điều tra hai required-test failures

N11 từng đạt 24/24 parity **không phải chứng chỉ ổn định**: Matplotlib-20859
có assertion phụ thuộc elapsed thật; Requests-1921 có failure cookie lịch sử
chưa xác định nguyên nhân và gọi HTTP service công cộng. Lượt chẩn đoán cố định
mới có bốn official gold controls resolved và bốn target observations pass;
không dùng các lần pass này để xóa failure trước hoặc qualify N7.

`eval_modes.py` sau run/collect N11/N11max tạo `scientific-validity.json` và
trả exit 2 khi selection có known instability, dù raw report hiện tại pass.
File giữ `research_score=null`, bảo toàn raw official reports và mẫu số.
Chạy audit riêng offline: `python probes/stability_audit.py --out RETAINED_RUN`.
Các probe được gọi trực tiếp và production web chưa tích hợp gate này.

[Điều tra, giới hạn và hướng sửa protocol](evidence/2026-10-22-stability-diagnosis/README.md).
Đây là chốt chống báo độ tin cậy sai; chưa khắc phục tận gốc sự bất định của
benchmark gốc. Không chỉnh test, ép clock/warning hay chọn repetition pass.

### Thử predictions thật trên một shard N7merge

`probes/n7_predictions.py` là entry point riêng cho 1–3 predictions thật cùng
một shard N7merge; không thay giới hạn gold-control của các probe cũ. Chạy trên
profile `phamvanvuhoan`, một VM 16 core/64 GiB, 8 worker, timeout test 600s,
lifetime 900s, envelope $1.05; không build snapshot, pull image hoặc tự retry.
Phải chốt SHA-256 file input trước `prepare`; dataset và catalog được pin.
`run` giữ nguyên official harness/evaluator/test, không dùng observer hoặc
protocol deterministic. Patch rỗng không tạo official report giả và vẫn nằm
trong mẫu số; thiếu report của patch không rỗng là lỗi/incomplete.

```bash
python probes/n7_predictions.py prepare --out NEW_RUN --source SOURCE --predictions INPUT.jsonl --expected-sha256 INPUT_SHA256 --dataset PINNED_VERIFIED_JSON
python probes/n7_predictions.py run --out NEW_RUN --source SOURCE
python probes/n7_predictions.py collect --out NEW_RUN --billing-name billing-first.json
```

Collect billing lại sau ít nhất 60 giây, so tiền theo đúng app và xác nhận VM
list rỗng trước báo chi phí. Model predictions và raw output giữ trong `runs/`
private, không tự đưa lên GitHub. Trial trên input thật không qualify N7 hoặc
suy ra điểm Verified đầy đủ.
