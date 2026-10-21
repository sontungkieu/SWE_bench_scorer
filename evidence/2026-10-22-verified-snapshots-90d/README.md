# 500 Verified images: 11 VM filesystem snapshots, TTL 90 ngày

Đã tạo và khôi phục thành công **11 snapshot**, bao phủ đúng **500 instance**
của SWE-bench Verified đã pin. Mỗi snapshot được tạo với `ttl=7776000` giây.
Để dùng được cả bộ, dùng trước **20/01/2027 04:23:45 (UTC+7)**; đây là deadline
bảo thủ của snapshot đầu tiên. Đọc lại không gia hạn TTL.

[Catalog dùng lại](usable-shards-catalog.json) chứa Image ID, deadline và mapping
instance → Docker image của từng nhóm. Chọn profile `phamvanvuhoan`, runtime
`vm` và snapshot Image ID của nhóm chứa instance cần chấm. Source workflow:
[snapshot_shards.py](../../probes/snapshot_shards.py),
[hướng dẫn](../../probes/SNAPSHOT_CATALOG.md).

| Nhóm | Image | Filesystem nguồn (GiB) | Docker ready khi khôi phục (giây) |
|---|---:|---:|---:|
| 0 | 100 | 33,51 | 13,53 |
| 1 | 100 | 28,69 | 18,74 |
| 2 | 59 | 37,27 | 14,19 |
| 3 | 8 | 35,78 | 16,58 |
| 4 | 9 | 36,28 | 12,03 |
| 5 | 9 | 32,54 | 15,31 |
| 6 | 27 | 36,16 | 14,39 |
| 7 | 21 | 37,63 | 10,97 |
| 8 | 43 | 36,92 | 10,40 |
| 9 | 100 | 35,48 | 11,26 |
| 10 | 24 | 9,26 | 5,33 |

500/500 reference, image ID và chuỗi diff-ID được kiểm tra trên VM khôi phục
mới. 22 lượt `/bin/true` với network bị tắt bao phủ 12 repo; không có Volume
hoặc registry secret trên VM kiểm tra. Dataset IDs và checksum khớp revision
`78f471bf655a3137b2e8a75af1501690ec009ec3`. SDK 1.6.2, SWE-bench 4.1.0 và runtime
`im-xE1XGZwwpKZrzcYW0wqjEI` được tái dùng. Không tải/build dependency local.

Lượt chia nhóm mất **5408,518 giây (90 phút 8,5 giây)**, gồm nạp, chụp, khôi phục
và kiểm tra. Đây là thời gian chuẩn bị một lần. Khoảng Docker ready trong bảng
không phải thời gian chấm patch. **Chưa chấm full 500 và chưa chuyển backend
web/CLI production sang bộ snapshot này.**

Lượt đầu nạp đủ 500 image vào một VM, filesystem dùng khoảng 301,13 GiB, nhưng
Modal trả `InternalError` khi chụp, mã `J5RJJ4HP`. Không có usable Image ID được
trả về. Docker đã shutdown sạch; VM đã tắt. Chưa xác nhận nguyên nhân hoặc
giới hạn size/inode từ provider. Probe nhỏ sau đó tạo snapshot 90 ngày thành
công. Bản chia nhóm lưu và kiểm tra từng nhóm trước khi làm tiếp; không có paid
retry tự động. Giới hạn 35 GiB trong planner là layer tar mỗi nhóm, không phải
giới hạn snapshot được Modal công bố.

| Khoản compute/egress, trước credits | USD |
|---|---:|
| Lượt gộp một VM bị lỗi snapshot | 0.53845001 |
| Probe TTL: số provider đang báo | 0.00000000 |
| Lượt chia 11 nhóm thành công | 0.55957331 |
| **Lần build này, gồm lượt lỗi** | **1.09802332** |

Nếu cộng thêm 14 app setup/cache/mock trên GCP (0.43935820) và pilot snapshot
24 bài trước đó (0.27996446), tổng **các mục được liệt kê** là **1.81734598 USD**.
Các lượt so sánh baseline VM/native trước đó được ghi riêng trong evidence của
pilot. Số ở đây không phải tổng chi tiêu account hoặc invoice sau credits.
Billing có độ trễ; giữ các lần đọc liên tiếp trong bundle để đối chiếu.
Lần đọc thứ hai lúc 22:51:07 UTC và thứ ba lúc 22:54:43 UTC đều ghi nhận
0.55957331 USD cho app chia nhóm. Lần đọc đầu báo 0.55619739 USD và được giữ
lại để thể hiện độ trễ; không dùng số đó làm kết quả cuối.

Tổng filesystem nguồn của 11 VM là 359,50 GiB, có tính lặp phần dùng chung.
Đây **không phải** dung lượng snapshot vật lý, unique storage hoặc số byte tính
phí. Phí lưu Image snapshot chưa được xác nhận từ bảng giá công khai; không áp
giá Volume lên Image. Đối chiếu [snapshot retention](https://modal.com/docs/guide/sandbox-snapshots)
và [pricing](https://modal.com/pricing). Toàn bộ 22 VM của lượt chia nhóm và hai
VM của lượt lỗi/probe đều đã tắt; danh sách VM của cả ba app đều rỗng.

84 kiểm tra offline và 14 unit check trên GCP đã pass. Đã đối chiếu hash source
trong VM, mọi plan/manifest/restore receipt, 500 unique instance IDs và metadata
của từng nhóm. [Verification](verification.json), [summary](summary.json),
[cleanup](cleanup-all-apps.json). Hai plan được gzip để giữ nguyên byte và giảm
metadata; raw bundle chứa cả plan từng nhóm và receipts gốc, không chứa binary
Docker image, model, prediction hoặc credential.

Mã thực thi được đóng gói từ Git index dựa trên `b48669c`, diff SHA-256
`6ae27cb42626d562b81f904bd94ef2467bf44d775be50f0ef3148590468ac9f2`.
[Context](source-context.json) và [patch](executed-source-context.patch.gz) bảo toàn
đúng source đã chạy. Commit mới bị GPG/Pinentry timeout; signing không bị tắt.
Tại thời điểm chốt artifact, code chia nhóm và evidence chưa được push; remote
`main` mới nhất là `b48669c5543c5ccbc46f0fa5cd6dd8c186d3ce2d`.
