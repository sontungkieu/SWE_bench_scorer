# Bằng chứng kiểm thử

`2026-10-21-v0.3.0` ghi hai vòng thử nghiệm cache trên các VM độc lập.
`oci-*` là định dạng OCI dùng trong bản phát hành: image nạp từ registry rồi
khôi phục với `--require-cached`, mock pass 3/3. Các file không có tiền tố OCI
là prototype Docker-save ban đầu; không dùng số đo đó để hứa hiệu năng full.
Lượt prototype prewarm được dừng có chủ đích để thay bằng direct OCI download;
manifest đã commit vẫn giữ. Một lỗi HTTP redirect ở canary đã được sửa và có
regression test không chuyển registry Authorization sang CDN. `verification.json`
và receipt phân biệt qualification, tiến độ cache và full benchmark evaluation.
Source hash là bằng chứng source thực sự chạy; các canary trước release có thể
khác version/source metadata với commit cuối. Không chứa registry/Modal token.

`2026-10-21-v0.1.0` là snapshot của run `mock-v0_1_0-20261021-02`,
profile Modal `phamvanvuhoan`, app
<https://modal.com/apps/phamvanvuhoan/main/ap-GhMCKwFJFTsYeTu1EWU3aA>.
Source code tại commit `39dc635`; hash từng source trong receipt khớp nội dung
v0.1.0. v0.1.1 thay version và chuẩn hóa LF/checksum; thuật toán grading không
đổi. Hash core.py trong receipt vẫn chỉ về source v0.1.0 đã chạy cloud.

Ba nhánh của cùng instance `django__django-11099`:

| Control | Kết quả | FAIL_TO_PASS | PASS_TO_PASS |
| --- | --- | --- | --- |
| Gold patch công khai | resolved | 3/3 pass | 19/19 pass |
| Diff hợp lệ không sửa bug | unresolved | 1/3 pass, 2 fail | 19/19 pass |
| Patch rỗng | unresolved | harness bỏ chạy test | harness bỏ chạy test |

Đây là phép kiểm tra hạ tầng/format/grading, không phải ba bài độc lập, không
phải điểm VAI hay điểm SWE-bench Verified 500 bài. Full mode chưa được chạy.

`receipt.json` ghi runtime Modal image ID, dataset revision, source hashes,
hash archive log gốc, exit code và trạng thái terminate. Official report của
gold/wrong được giữ nguyên; empty không có per-instance report theo hành vi
bỏ qua empty patch của official harness. `empty-summary.json` ghi nhánh này.

Archive log đầy đủ kèm SHA-256 có trong release v0.1.0. Không cần đưa
`results.tar.gz`, cache Docker hoặc credentials vào Git. `SHA256SUMS.json`
kiểm tra các file evidence (không tự hash chính manifest).
