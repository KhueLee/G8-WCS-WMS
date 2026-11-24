# Báo cáo test – G8 luồng B v0.2.0 (24/11/2026)

**Máy test:** Windows 11, Python 3.13, MongoDB 4.4.29 (mongod kèm theo).
**Kết quả lượt cuối:**
- v0.2.0: **19/19 PASS** (17 kịch bản đầu cuối + 2 với phần mềm trạm thật).
- v0.1.0: 16/16 PASS.

## 1. Test đầu cuối với simulator của B (`run_tests.bat` → `tests/e2e_test.py`)

Hệ thống được dựng trên cổng và DB riêng (`e2e_*`):
- WMS ảo, WCS B;
- RCS giả: 2 robot, gửi callback tới **2 URL** (giống trường hợp RCS khai nhiều mục), chỉ nhận điểm có trong danh sách vị trí;
- bàn định danh giả MCSX009;
- băng tải xuất giả BCXB001.

| # | Kịch bản | Kiểm tra | KQ |
|---|---|---|---|
| s01 | Move nhóm 2 bước `1/2`, `2/2` | Chạy tuần tự. WMS nhận 2 `success`. orderId dạng `VB-…`, không chứa `MovePallet`. Trạng thái ô cập nhật ở WCS và WMS | PASS |
| s02 | RCS báo thất bại (status 7) ở bước 1 | Bước 1 FAILED, bước 2 SKIPPED, WMS nhận 2 `false`, ô đích được nhả. Bấm *Chạy lại* → cả hai bước thành công | PASS |
| s03 | Vị trí không có trong danh sách / `Type_Robot_ID` sai | FAILED (LocationFailed / ModelCodeFailed), báo WMS `false` | PASS |
| s04 | Gửi lặp Task_ID | Payload giống → 200; khác → 201; thiếu trường → 201 `Missing …` | PASS |
| s05 | PO 2 pallet × 3 thùng | WCS → trạm (1 pallet / bản tin) → `pallet_identify_done` → `Finished_Goods` → `confirm_product` (Status true + Location) → WMS tự tạo Move cất kệ → pallet nằm đúng ô đã gán | PASS |
| s06 | ASN 1 pallet | `identify-import`, `ASN_ID`, hoàn tất | PASS |
| s07 | Hủy PO khi trạm còn 3 pallet chờ | Trạm hủy pallet chờ, WCS chuyển `CANCELLED` | PASS |
| s08 | DO 2 pallet (1 pallet xuất thiếu: 2 / 3 thùng) | Trạm báo từng pallet → `OB_Pallet` → WMS trừ đúng số thùng; DO `COMPLETED`, line rảnh | PASS |
| s09 | WMS lỗi 503 hai lần với `Finished_Goods` | Outbox gửi lại, lệnh vẫn hoàn tất | PASS |
| s10 | Line không thuộc B; tắt line (cutover) | 201 "không thuộc WCS B" / "đang tắt"; bật lại được | PASS |
| s11 | Callback RCS của luồng A (`…MovePallet…`) và orderId lạ | Trả 1000, không ảnh hưởng task của B | PASS |
| s12 | **Tắt RCS callback**, tắt WCS B khi task đang chạy, RCS xong trong lúc WCS tắt | WCS khởi động lại, hỏi `getTaskOrderStatus`, báo WMS `success` | PASS |
| s13 | Tắt WMS ảo khi task đang chạy | Task xong, chưa báo được; WMS lên lại → outbox gửi bù | PASS |
| s15 | Move tay trên WCS B (2 bước) + bản tin WMS dán tay | Robot chạy xong, `reported = MANUAL`, không sinh bản tin WMS; thiếu pallet thì báo lỗi | PASS |
| s16 | Sửa cấu hình qua web | Thêm line → có trong DB và file, `poll_s` áp dụng ngay; URL sai bị từ chối; xoá line; WMS thêm điểm sàn (tự chuẩn hoá mã); lưu lại danh sách vị trí | PASS |
| s17 | Dashboard | `/api/dashboard` của WCS và WMS: 24 cột theo giờ, ô số liệu khớp, zone B đủ 20 ô; trang và file tĩnh trả 200 | PASS |
| s14 | Hai task cùng ô đích | Task 2 chờ khi ô đang BUSY; ô FULL → task 2 FAILED (ô kệ phải trống) | PASS |

## 2. Test với phần mềm trạm G8 thật (`tests/g8_modules_test.py`)

| # | Phần mềm | Cách chạy | Kiểm tra | KQ |
|---|---|---|---|---|
| m01 | **G8-Export** (main, 26/10) | App thật; PLC và camera dùng driver `simulator`. `UrlScs` / `UrlWms` trỏ về WCS B (chỉ đổi cấu hình). Thùng đi qua bằng `tools.simulator box` (2 camera gửi mã qua TCP) | G8-Export nhận `/scs/orderTask` (code 1000), quét 4 thùng, báo 2 pallet về `/export-service/scs/taskStatusReport`. WCS B báo `OB_Pallet`, DO `COMPLETED` | PASS |
| m02 | **G8-ID v2.4.12** | App thật ở **MANUAL** cùng công cụ mô phỏng của G8-ID; công nhân ảo tự đặt thùng và bấm ĐỊNH DANH. WCS B gửi lệnh vào cổng 8500 | G8-ID nhận 2 `orderTask` (1000 SUCCESS); cân 1635 g (chuẩn 1637 ± 100), in tem, đọc lại khớp mã; chốt 2 pallet. WCS B báo `Finished_Goods` và gửi `confirm_product` (HTTP 200) | PASS |

Ghi chú về m02: ở MANUAL, G8-ID gửi `pallet_identify_done` về công cụ mô phỏng của chính nó (thiết kế của G8-ID). Vì vậy test chuyển tiếp nguyên body đó sang WCS B. Ở AUTO, trạm gửi thẳng tới `pallet_done_url`, tức là URL đổi lúc cutover.

## 3. Chạy thử trên cổng thật (cùng máy, simulator)

Bản chạy thử dùng WCS B :9000, WMS ảo :9100, RCS giả :7100, bàn định danh giả :9501, băng tải giả :9701.
- Gửi 1 PO (2 pallet × 4 thùng) → định danh xong, robot giả cất 2 pallet vào S02-250-1 / S02-250-2.
- Xuất 3 thùng → DO `COMPLETED`.
- Giao diện cả hai app mở được, hiển thị đúng; đã chụp bằng Edge headless.

Giao diện v0.2.0 đã chụp bằng Edge headless ở các trang Dashboard, Cấu hình, Đặt lệnh, Xuất DO, ở độ rộng 1500 px và 600 px. Không bị tràn ngang, không chồng chữ.

## 4. Phát hiện khi test

1. Máy dev có `HTTP_PROXY` nên urllib của G8-ID gửi cả `127.0.0.1` qua proxy. Công cụ mô phỏng G8-ID khi đó không nối được app chính. WCS B / WMS ảo không bị ảnh hưởng vì dùng `trust_env=False`. Nếu máy trạm có proxy thì cần `NO_PROXY`.
2. G8-ID nhận lệnh trước khi thiết bị nối xong thì không tự START được ("KIỂM TRA LẠI KẾT NỐI MÁY IN"), phải bấm Bắt đầu. Đây là hành vi của trạm, không phải lỗi giao tiếp.
3. Đã sửa trong lúc test: index `order_id` (sparse vẫn chặn nhiều giá trị null → đổi sang partial index); route của bàn định danh giả; tab "Tạo lệnh" của WMS ảo không tự làm mới nữa.

## 5. Chưa test

- RCS thật và bàn định danh thật trên hiện trường (P2).
- Test chéo với các script `simu_*` của G8-WCS gốc (đang viết cứng IP 192.168.x).
- Tải lớn: mới chạy vài chục lệnh mỗi lượt.
