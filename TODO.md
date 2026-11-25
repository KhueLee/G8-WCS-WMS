# TODO – Luồng B (WMS ảo + WCS ảo) – cập nhật 24/11/2026

Trạng thái: **v0.6.0 (25/11/2026)**; Lee duyệt viết spec + code + test từ 24/11. v0.1.0 xong ngày 24/11: spec `docs/SPEC.md`, code `g8b/`, test `docs/TEST_REPORT.md`.

## Đã chốt (Lee, 24/11/2026)
- Luồng B chạy **hàng và kệ thật**, có **chia zone** riêng cho B.
- Đầu nhập: chưa làm. WCS không quan tâm phần cứng trạm (cân, in, PLC, camera là việc của phần mềm trạm).
- **Môi trường test phải có đủ các module G8 khác chạy ở chế độ simulation**, để test đầu cuối trước khi đụng hệ thật.
- **Không sửa phần mềm trạm** (G8-ID, G8-Export).
- Task RCS của luồng B **dùng callback** (spec ICS V3.0.6 §3.2.1).
- **12 bàn định danh.** RCS **areaId = 2**.
- Điểm lưu trữ rack trên RCS **đặt tên được**, có thể đặt theo mã vị trí WMS.

## Đã chốt thêm (Lee, 24/11 – lượt 2)
- Callback RCS: thêm 1 mục mới trên RCS, đặt tên riêng, trỏ về IP của máy chạy WCS B.
- Đổi được URL trên trạm. IP bàn định danh cấu hình được trong WCS B.
- Mã vị trí: dùng dấu `-` (vd. `A1-16-1`) ở cả file Excel, WMS và WCS. Không dùng `.`.
- Khóa được ô của zone B (để luồng A không dùng).
- Không phân biệt hàng thật hay hàng test.
- RCS dùng chung: mọi task của A và B đều đẩy vào cùng một RCS.

## Hệ quả thiết kế (đưa vào spec P0)
- `orderId` của B có tiền tố `VB-` và không chứa chữ `MovePallet`, vì WCS gốc lọc callback theo chữ này.
- Endpoint callback của B luôn trả `1000`. Callback của task không phải của B thì bỏ qua và ghi log, đề phòng RCS gửi cùng callback tới mọi URL.
- Vẫn giữ việc hỏi `getTaskOrderStatus` làm kênh dự phòng (WCS gốc cũng làm vậy).
- Gửi `cancelTask` đúng spec, body là mảng.
- Cutover = đổi URL trên trạm + gỡ line khỏi config WCS gốc + thêm line vào config WCS B.

## Đã chốt (Lee, 24/11 – lượt 3)
- Trạm cutover đầu tiên: 1 bàn định danh.
- Stack: FastAPI + MongoDB. Chạy bằng file `.bat` gọi Python, không đóng gói exe.
- Lắng nghe trên mọi IP của máy (`0.0.0.0`), không viết cứng IP. Địa chỉ của các hệ khác (RCS, trạm, WMS) để trong file cấu hình.
- Cả WMS ảo và WCS B đều có giao diện web để theo dõi và giám sát.
- Tên điểm trên RCS trùng mã vị trí WMS (vd. `A1-16-1`). `taskPath` gửi thẳng mã vị trí, không cần bảng mapping Excel. Danh sách vị trí chỉ dùng để kiểm tra mã hợp lệ và quản lý zone.

## Kiểm tra lúc nghiệm thu (không phải câu hỏi)
- [ ] RCS gửi callback tới mọi URL task-status đã khai, hay chỉ tới 1 URL? Phía B đã xử lý được cả hai trường hợp.
- [ ] Tên điểm có dấu `-` được RCS chấp nhận (spec ghi tên 4–10 ký tự chữ và số), và kệ FK nhiều tầng thì mỗi tầng có tên điểm riêng.
- [x] Cổng của B không trùng với WCS gốc (A đang dùng 8040, 8060, 8090, 8088) – B dùng 9000 / 9100 / 5510, Mongo 27018.

## P0 / P1 – đã làm (24/11)
- [x] Spec: `docs/SPEC.md` (WMS→WCS, WCS→WMS, device API, RCS, state machine, outbox, cutover).
- [x] WMS ảo (`g8b/wms.py`): Login, Pallet_Location, Finished_Goods, OB_Pallet, Location_Stocks; tạo PO / ASN / Move / DO / hủy; tồn kho, vị trí; giao diện web.
- [x] WCS B (`g8b/wcs.py`): Move (nhóm n/m, giữ chỗ ô, RCS callback + hỏi định kỳ, hủy / xong tay / chạy lại), định danh, xuất, outbox, line on/off (cutover); giao diện web.
- [x] Simulator RCS / bàn định danh / băng tải xuất (`g8b/sim.py`), cổng riêng không trùng A.
- [x] 14 kịch bản đầu cuối PASS (`tests/e2e_test.py`).
- [x] Test với **G8-Export thật** (driver simulator) và **G8-ID thật** (MANUAL + công cụ mô phỏng) PASS (`tests/g8_modules_test.py`).
- [x] 3 file `.bat`: `install.bat` (cài + kiểm tra môi trường), `run.bat` (chạy / stop / status / demo), `update.bat` (cập nhật từ GitHub). Phụ: `tools\run_demo.bat`, `tools\run_tests.bat`.

## v0.2 – v0.5 (24–25/11)
- [x] Dashboard, cấu hình và đặt lệnh trên web; MongoDB riêng cổng 27018.
- [x] Danh sách vị trí theo file highbay (form 14 cột + RAW), nhiều kho theo tiền tố, nạp có chọn điểm + xem khác biệt, xoá kéo theo pallet; xuất Excel dạng RAW.
- [x] WMS: danh mục sản phẩm (quy cách thùng / pallet), 4 loại lệnh (định danh, luân chuyển, xuất hàng, cập nhật vị trí), sửa tay pallet / ô.
- [x] Site giao diện chung :5510 (menu nhóm sổ xuống, hộp thoại lựa chọn, logo G8 HOME, nút hướng dẫn PDF).
- [x] Hướng dẫn sử dụng `docs/HUONG_DAN_SU_DUNG.pdf` (tools/build_guide.py).

## v0.6 (25/11)
- [x] Site giao diện cổng **5510**; MongoDB cache tối đa 1 GB.
- [x] Giao diện luôn nền sáng; ô kệ viền xanh lá = trống, viền cam = có pallet (ghi mã pallet), tab "Vị trí"; nút hướng dẫn PDF ở cuối cột trái; file giao diện gửi `Cache-Control: no-cache`.
- [x] Chạy được trên **Python 3.10+** (máy đích dùng 3.10): `fastapi 0.162.5`, `pymongo 4.18.3`; test 24/24 trên 3.10 và 3.13.
- [x] Gom còn 3 file bat: `install.bat`, `run.bat`, `update.bat` (+ `g8b/ctl.py`).
- [x] Lee chốt 25/11: TKELog dùng mã vị trí dấu `.`, luồng B dùng `-`, bản tin từ TKELog tự đổi sang `-` (test s22). Kho S chỉ dùng robot đội gầm (latent) = 2.

## Còn lại
- [ ] Đổi đích task đang chạy (`questionLocationNew` → `Location_Stocks` + RCS `updateOrderPointInfo`).
- [ ] API 6 (ASN confirm bằng PDA) – WCS gốc cũng chưa bật. API 9 (đóng lệnh SX) – WCS gốc gắn chung endpoint hủy định danh; hỏi TKSolution có tách riêng không.
- [ ] Test chéo với các script giả lập của G8-WCS gốc (`scripts/simu_*`, đang viết cứng IP 192.168.x) và `g8-ops-monitor mock_ics`.
- [ ] Đăng nhập cho giao diện web (hiện mở trong LAN như các tool G8 khác).
- [ ] Ở hiện trường: thêm mục callback trên RCS, đặt tên điểm zone B, khoá ô zone B trên TKELog, chọn bàn định danh đầu tiên (Line_ID + IP), cấu hình `config/*.json`, chạy thử 1 PO.
- [ ] P2: Cutover 1 bàn định danh thật trên zone B theo `docs/SPEC.md` mục 8, có rollback.
