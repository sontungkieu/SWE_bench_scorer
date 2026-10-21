# Bằng chứng kiểm thử

`2026-10-21-v0.1.0` là snapshot của run `mock-v0_1_0-20261021-02`,
profile Modal `phamvanvuhoan`, app
<https://modal.com/apps/phamvanvuhoan/main/ap-GhMCKwFJFTsYeTu1EWU3aA>.
Source code tại commit `39dc635`; hash từng source trong receipt khớp nội dung
v0.1.0. Những commit sau chỉ bổ sung hướng dẫn và bằng chứng phát hành.

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
