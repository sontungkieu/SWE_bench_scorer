# SWE-bench Scorer v0.1.1

Bản vá chuẩn hóa LF và tính lại checksum fixture/evidence sau read-back từ
GitHub. Hành vi chấm giữ nguyên v0.1.0. Log/receipt cloud đính kèm vẫn là
bằng chứng nguyên bản của v0.1.0, không gắn nhãn lại thành một lần chạy mới.

Trình chấm `preds.jsonl` bằng official SWE-bench harness trên Modal VM CPU.
Máy cá nhân chỉ validate input, gọi Modal và nhận artifact; không chạy Docker,
không tải checkpoint và không cần GPU cho khâu chấm.

Gồm mock gold/wrong/empty cho bài thật `django__django-11099`, hướng dẫn
PowerShell/WSL, billing guard, dataset revision và receipt/hash để đối chiếu.

Kiểm chứng offline: 7/7 test ở v0.1.1 (thêm regression test checksum).
Bằng chứng mock cloud và source hash của v0.1.0 có trong
`evidence/2026-10-21-v0.1.0`; log gốc nằm trong release asset. Ba control phải
cho kết quả gold resolved, wrong unresolved, empty unresolved.

Đây là kiểm tra hạ tầng trên một bài, không phải điểm model. Full 500 bài
chưa chạy; chế độ full cần ngân sách và kiểm tra thêm tài nguyên cho workload
thực tế. `--estimate-usd` là input cho guard, không phải hard billing cap.

Đọc README trước khi chấm file thật. Không có API token/credential trong repo
hoặc release asset.
