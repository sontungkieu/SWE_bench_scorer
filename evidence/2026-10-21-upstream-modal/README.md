# Thử backend `--modal true` chính thức

Ngày thử: 2026-10-21, khoảng 19:34–19:39 giờ Việt Nam. Profile
`phamvanvuhoan`; SWE-bench 4.1.0; Modal SDK 1.6.2. Đây là phép thử riêng,
không thay đổi backend Docker, web hoặc cache đang được triển khai.

**Kết quả: backend nguyên bản chưa chấm được gold control trên stack này.**
Image của bài đã build xong, nhưng khởi tạo runtime lỗi trước khi áp patch
và chạy test:

```text
ModalSandboxRuntime.__init__
  self.write_file("/sys/fs/cgroup/cpu/cpu.shares", "2048")
write_file
  self.sandbox.open(file_path, "w").write(content)
AttributeError: 'Sandbox' object has no attribute 'open'
```

SDK 1.6.2 hiện có `Sandbox.filesystem`, không có `Sandbox.open`. Không sửa
harness, test hoặc grading trong phép thử này. Không gọi đây là kết quả
unresolved của model: chưa có test nào chạy và không có instance report.

## Cấu hình và kiểm chứng

- Coordinator chạy trong venv riêng trên GCP hiện có; không cài dependency
  trên laptop hoặc vào venv của dịch vụ web. Không chạy Docker trên GCP.
- Dùng cùng fixture `django__django-11099` và dataset revision
  `78f471bf655a3137b2e8a75af1501690ec009ec3` như scorer hiện tại. Đối chiếu
  checksum cả ba fixture và gold patch với dataset trước khi gọi CLI.
- CLI gọi `python -m swebench.harness.run_evaluation ... --modal true`,
  một instance, `--timeout 600`, giới hạn coordinator 900 giây/control.
  Lệnh đầy đủ ở [command.json](command.json).
- Lần đầu dừng trước khi tạo app: validator của SWE-bench chỉ kiểm tra
  `~/.modal.toml`, bỏ qua `MODAL_CONFIG_PATH`. Lần tiếp theo dùng HOME tạm
  của riêng tiến trình và symlink tới config scoped sẵn có. Không chép,
  sửa hoặc log credential; symlink đã được dọn khi tiến trình kết thúc.
- Gold mất **283.7538207639009 giây**, gồm cold build và setup. CLI trả
  **0**, nhưng aggregate chính thức ghi **0 completed, 1 error**. Probe
  đánh dấu fail và trả **1**. Không chạy tiếp wrong/empty sau lỗi hạ tầng.
- Image worker có lệnh upstream không pin `pip install swebench tenacity`,
  thực tế cài distribution **5.0.2**; Modal cũng upload source `swebench`
  của coordinator qua `include_source=True`. Đây là package drift quan sát
  được, không phải nguyên nhân lỗi đã chứng minh. Traceback thực tế nằm
  trong backend source 4.1.0 được upload.
- App `ap-AFiVtnJqIEUMBtmjIbFsHQ` đã **stopped**, tasks **0**. Lệnh stop
  bổ sung trả 1 vì app đã dừng. SDK liệt kê sandbox thuộc app sau đó trả
  danh sách rỗng; không can thiệp app của các lượt chấm khác.
- Billing snapshot lúc 19:40:06 giờ Việt Nam ghi riêng app này
  **$0.01946276**, trước credits, chưa phải hóa đơn cuối. Dự trù guard là
  $0.50, không phải provider hard cap. [Chi tiết billing](billing.json).

## Ý nghĩa đối với lựa chọn backend

`--modal` tồn tại và thực sự điều phối công việc trên Modal. Tuy nhiên,
SWE-bench 4.1.0 + Modal 1.6.2 chưa chạy nguyên bản thành công trong phép thử
này; cần xử lý tương thích trước khi thay backend hiện tại. Chưa chứng minh
phải giữ toàn bộ runner/cache riêng hoặc rằng native Modal luôn không dùng
được. Không có benchmark tốc độ/chi phí đối chiếu; chưa chạy full 500 bài.

TODO `TestSpec` ở nhánh upstream mới nhất không áp dụng trực tiếp cho
4.1.0: source của 4.1.0 vẫn có `setup_env_script` và `install_repo_script`.
Lỗi thực nghiệm ở đây là API `Sandbox.open`.

## Bằng chứng và chạy lại

[verification.json](verification.json) ghi outcome, source hashes và cleanup;
[summary.json](summary.json) là kết quả probe nguyên bản;
[official-aggregate.json](official-aggregate.json) là aggregate của harness;
[failure.log](failure.log) giữ traceback. `gold-console.log.gz` giữ toàn bộ
log gold; [coordinator-packages.json](coordinator-packages.json) ghi dependency.
Full archive giữ local ở `runs/upstream-modal-20261021-02/`, không gồm
credential hoặc dataset tải về.

Script [upstream_modal_probe.py](../../probes/upstream_modal_probe.py) được
so hash byte-for-byte với bản đã chạy. Nó yêu cầu Python/Linux remote có
SWE-bench 4.1.0, Modal SDK 1.6.2 và `ghapi<2`; billing guard cần pass trước
khi gọi. Không tự cài dependency hoặc cấu hình credential trên máy cá nhân.
Ví dụ trên coordinator đã chuẩn bị, dùng run directory mới:

```bash
MODAL_CONFIG_PATH="$HOME/.config/swebench-web/modal.toml" \
MODAL_PROFILE=phamvanvuhoan PYTHONUNBUFFERED=1 \
venv/bin/python probe.py --fixtures mock --output upstream-modal-NEW-ID
```

Nguồn upstream: [backend v4.1.0](https://github.com/SWE-bench/SWE-bench/blob/v4.1.0/swebench/harness/modal_eval/run_evaluation_modal.py),
[TestSpec v4.1.0](https://github.com/SWE-bench/SWE-bench/blob/v4.1.0/swebench/harness/test_spec/test_spec.py).
