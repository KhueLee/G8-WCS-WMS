# G8 luồng B – Spec WMS ảo + WCS B (v0.1.0, 24/11/2026)

Tài liệu mô tả đúng hành vi của mã nguồn trong repo này (`g8b/`). Các quyết định đã chốt với Lee: `TODO.md`.

## 1. Phạm vi và nguyên tắc

- **Hai luồng độc lập.**
  - Luồng A: WMS TKELog ↔ G8-WCS gốc.
  - Luồng B: WMS ảo ↔ WCS B (repo này).
  - **RCS HuaRay dùng chung**: mọi task của A và B đều vào cùng một RCS, areaId 2.
- **Không sửa phần mềm trạm.** WCS B nói đúng device API mà G8-ID / G8-Export đang dùng với WCS gốc. Chuyển trạm A ↔ B chỉ là đổi URL (mục 7).
- **WMS ảo nói đúng API TKELog V5** theo hành vi thật của G8-WCS gốc. Nhờ vậy WCS B, nếu cần, có thể nối thẳng TKELog.
- **Mã vị trí** dạng `Rack-Cột-Tầng` với dấu `-` (vd. `S02-257-1`) ở mọi nơi. Mã nhận vào có dấu `.` được tự đổi sang `-`.
  - Tên điểm trên RCS trùng mã vị trí, nên WCS B gửi thẳng mã vào `taskPath`, không qua bảng mapping.
- **Công nghệ:** Python 3.11+, FastAPI, MongoDB 4.4+.
  - Chạy bằng file `.bat` gọi Python, không đóng gói exe.
  - Nghe ở `0.0.0.0`, không viết cứng IP. Mọi địa chỉ nằm trong `config/*.json`.

## 2. Thành phần và cổng

| Thành phần | Lệnh | Cổng mặc định | Giao diện |
|---|---|---|---|
| WCS B | `run_wcs.bat` → `python -m g8b.wcs` | 9000 | `http://<máy>:9000` |
| WMS ảo | `run_wms.bat` → `python -m g8b.wms` | 9100 | `http://<máy>:9100` |
| RCS giả | `python -m g8b.sim rcs` | 7100 | `/` (trạng thái) |
| Bàn định danh giả | `python -m g8b.sim ids` | 9501 | `/` |
| Băng tải xuất giả | `python -m g8b.sim scs` | 9701 | `/` |
| MongoDB | `start_mongodb.bat` (mongod 4.4 kèm theo) | 27017, chỉ 127.0.0.1 | – |

Cổng của B không trùng với WCS gốc (8040, 8060, 8090, 8088) hay trạm (8500, 8600, 8700, 8080).

## 3. API WMS → WCS B (giống WCS gốc)

Body là JSON thô như WCS gốc nhận. Dạng `{Auth, Data}` cũng được chấp nhận.

WCS B trả HTTP 200 kèm:
- `{"code":200,"desc":"success"}` khi nhận lệnh;
- `{"code":201,"desc":"<lý do>"}` khi từ chối.

| Đường dẫn | Ghi chú |
|---|---|
| `POST /move-service/task/wms/move_pallet` | Xem 3.1 |
| `POST /identify-service/wms/task/identify-cartons` | PO (thành phẩm). Có `Item_Information[]`, mỗi phần tử là 1 pallet |
| `POST /identify-service/wms/task/identify-import` | ASN (hàng thương mại). Dùng `ASN_ID` thay `PO_ID` |
| `POST /identify-service/wms/task/identify-cartons/cancel` | `{PO_ID}` |
| `POST /export-service/task/wms/export_items` | DO |

### 3.1 Move

- **Trường bắt buộc:** `Task_ID, WH_Code, Pallet_ID, Type_Robot_ID, Type_Move_ID, Location_Old, Location_New`.
- **Nhóm task:** `Task_ID` dạng `n/m_<khoá nhóm>`. Nhóm chỉ chạy khi đã nhận đủ m task, và chạy tuần tự từ 1 đến m.
  - Task không đúng dạng thì được coi là nhóm `1/1` (WCS gốc thì bỏ qua task loại này).
- **Gửi lặp:** cùng `Task_ID` với payload y hệt trả 200 (idempotent). Cùng `Task_ID` nhưng payload khác trả 201.
- **Chọn robot:** `Type_Robot_ID` 1 → `forkAutoTemplate2`, 2 → `liftMoveShelf2` (cấu hình được).
- **Ưu tiên:** `Type_Move_ID` 2 hoặc 14 dùng priority 4 trên RCS, còn lại 6.
- **Kiểm tra trước khi gửi RCS:**
  - Hai vị trí phải có trong danh sách và không bị khoá.
  - Ô kệ đích phải trống và được giữ chỗ (`BUSY`) một cách nguyên tử. Điểm trạm chỉ cần không bận.
  - Nếu ô đích đang có task khác giữ chỗ thì chờ.
  - Mỗi pallet chỉ có một task đang chạy tại một thời điểm.

## 4. API WCS B → WMS (TKELog V5)

- **Đăng nhập:** `POST /API/General/Sys/Login` với `{Auth:{Token:"",App_Name}, Data:{User_Name, Password}}` → `data.token_ID`.
- Mọi bản tin khác có dạng `{Auth:{Token,App_Name}, Data:{...}}`.
- **Thành công** khi HTTP 200 và `message.message_Code == 200`.
- **Mã 203** (token hết hạn): WCS B đăng nhập lại rồi gửi lại.
- **Mã khác** (201–214): WMS từ chối hẳn. Bản tin chuyển sang `DEAD`, hiện ở tab Cảnh báo; với định danh thì WCS B báo lỗi xuống trạm.

| Bản tin | Đường dẫn | Data |
|---|---|---|
| Xác nhận Move | `/API/G8/Customer/Update/Pallet_Location` | Payload Move gốc kèm `status: "success" \| "false"` |
| Pallet định danh xong | `/API/G8/Customer/Update/Finished_Goods` | `{PO_ID \| ASN_ID, WH_Code, Line_ID, Pallet_ID, Ctn}`. WMS trả `data.Location` = ô cất pallet |
| Pallet đã xuất | `/API/G8/Customer/Update/OB_Pallet` | `{DO_ID, WH_Code, Conveyor_Belt, Item_Information:[{Pallet_ID, Item_Code, Ctn, Carton_List}]}` |
| Hỏi ô trống | `/API/G8/Customer/Update/Location_Stocks` | `{WH_Code, Task_ID, Location}` → `data.Status`: `false` = trống |

## 5. Device API (không đổi so với WCS gốc)

### 5.1 Bàn định danh (G8-ID, `DOCKING_PROTOCOL.md`)

| Chiều | Đường dẫn | Body |
|---|---|---|
| WCS → trạm | `POST {url}/orderTask` | `{Task_ID, PO_ID, PO_Name, WH_Code, Line_ID, info_detail:[1 pallet], Pallet_Current, fromSystem}`. Mỗi pallet một bản tin, `Task_ID = "<số pallet>-<PO_ID>__HH:MM:SS.ffffff"` |
| WCS → trạm | `POST {url}/cancelTask` | `{Task_ID, PO_ID, Line_ID, fromSystem}`. Trạm hủy các pallet đang chờ của PO và giữ pallet đang làm |
| WCS → trạm | `POST {url}/confirm_product` | `{PO_ID, Line_ID, Pallet_ID, Location, Status:"true"\|"false", Message}` |
| Trạm → WCS | `POST /identify-service/ids/pallet_identify_done` | `{Task_ID, PO_ID, WH_Code, Line_ID, palletID, Ctn}`. WCS luôn trả `{"code":1000}` (G8-ID chỉ xét HTTP 2xx) |

- **Gửi thành công** khi HTTP 200 và `code == 1000`.
- **Từ chối hẳn** (không gửi lại) khi trạm trả HTTP 400 kèm 1001.
- **Gửi lặp** `pallet_identify_done` không sinh bản tin WMS thứ hai.

### 5.2 Đầu xuất (G8-Export)

| Chiều | Đường dẫn | Body |
|---|---|---|
| WCS → trạm | `POST {url}/orderTask` | Payload DO của WMS kèm `fromSystem`. Mỗi line chỉ chạy một DO tại một thời điểm |
| Trạm → WCS | `POST /export-service/scs/taskStatusReport` | `{DO_ID, WH_Code, Conveyor_Belt, Pallet_ID, Item_Code, Ctn, Ctn_Need, Carton_List}`, có header `Idempotency-Key` |

Khi đã báo WMS đủ mọi pallet của DO thì DO chuyển `COMPLETED` và line rảnh. Có nút "Đóng tay" để kết thúc một DO xuất thiếu.

### 5.3 RCS (ICS HuaRay V3.0.6)

| API | Ghi chú |
|---|---|
| `POST /ics/taskOrder/addTask` | `{modelProcessCode, priority, fromSystem:"WCS-B", orderId, taskOrderDetail:[{taskPath:"<từ>,<đến>"}]}`. `1000` hoặc `2111` (orderId đã có, gửi lại sau khi khởi động lại) đều coi là đã gửi |
| `POST /ics/out/task/cancelTask` | Body là **mảng** `[{orderId}]` đúng spec (WCS gốc gửi object) |
| `POST /ics/out/task/getTaskOrderStatus` | Hỏi mỗi `poll_s` giây cho các task đang chạy. Là kênh dự phòng cho callback |
| Callback `POST /rcs/taskStatusReport` (alias `/move-service/rcs/taskStatusReport`) | Khai một mục mới trên RCS (Third-party Service Configuration), trỏ về `http://<IP máy WCS B>:9000/rcs/taskStatusReport` |

- **`orderId` của B:** `VB-0000001`… Không chứa chữ `MovePallet`, vì WCS gốc chỉ nhận callback có chữ này, nên hai bên không lẫn task của nhau.
- **Callback task không phải của B:** bỏ qua nhưng vẫn trả `1000`, để chạy đúng cả khi RCS gửi callback tới mọi URL đã khai.
- **Ánh xạ trạng thái:**
  - `8` → SUCCEEDED, `7` → FAILED, `3` → CANCELLED.
  - `6/9/10/20–23` → RUNNING.
  - `1/4/5` → chờ.

## 6. State machine

**Move:**

```
CREATED ──(đủ nhóm, task trước SUCCEEDED, ô đích giữ được)──► DISPATCHED ──RCS 6──► RUNNING
   │                                                              │
   │                                                              ├─ RCS 8 / Xong tay ──► SUCCEEDED
   │                                                              ├─ RCS 7, RCS từ chối, vị trí sai, robot sai ──► FAILED
   │                                                              └─ RCS 3 (Hủy tay → cancelTask) ──► CANCELLED
   └─ task trước trong nhóm FAILED / CANCELLED ──► SKIPPED
FAILED / CANCELLED / SKIPPED ──(Chạy lại)──► CREATED (attempt+1, orderId RCS mới)
```

- Mọi trạng thái cuối (SUCCEEDED, FAILED, CANCELLED, SKIPPED) đều đưa bản tin `Pallet_Location` vào outbox. `reported = true` khi WMS xác nhận; đây chính là bước REPORTED của phương án.
- Khi xong:
  - SUCCEEDED: ô cũ về `EMPTY`, ô mới `FULL` kèm pallet.
  - Lỗi hoặc hủy: nhả chỗ giữ ở ô đích.

**Định danh** (mỗi pallet):
- `SENT → IDENTIFIED (trạm báo xong) → REPORTED (WMS nhận) | WMS_REJECTED`.
- Hủy: các pallet chưa làm chuyển `CANCELLED`.
- Lệnh: `RUNNING → COMPLETED | CANCEL_REQUESTED → CANCELLED`.

**Xuất (DO):** `QUEUED → RUNNING → COMPLETED | FAILED (trạm từ chối) | CLOSED (đóng tay)`.

## 7. Outbox, idempotency, khởi động lại

- Mọi bản tin gửi đi (WMS, trạm) nằm trong collection `outbox` với `key` duy nhất. Gửi lại với backoff 5 → 60 s, không giới hạn số lần.
- Job đang `SENDING` lúc tắt máy sẽ được gửi lại khi chạy lại. Job `DEAD` gửi lại tay được ở tab Outbox.
- Lệnh lưu MongoDB trước khi ACK.
- Khi khởi động lại, task đã gửi RCS không gửi lại: WCS B tiếp tục hỏi `getTaskOrderStatus`, và nếu RCS trả `2111` thì coi là đã gửi.
- Mọi bản tin vào / ra đều ghi ở `messages` (giữ 14 ngày, xem ở tab "Bản tin") và trong `logs/<app>/<app>.log` (xoay theo ngày, giữ 30 ngày).
- Lệnh đã xong tự xoá sau 30 ngày (TTL) để DB không phình.

## 8. Cutover một bàn định danh A ↔ B

1. **Chuẩn bị:**
   - Thêm line vào `identify_lines` trong `config/wcs.json`: `line_id`, `url = http://<IP trạm>:8500/ids`, `location` (điểm sàn của bàn).
   - Thêm `stations` trong `config/wms.json`.
   - Khoá các ô zone B bên TKELog.
2. **Drain:** trạm không còn pallet đang làm. Trên TKELog không gửi lệnh mới cho line đó.
3. **Trên WCS gốc:** gỡ line khỏi `line_manufacture_config.json` rồi khởi động lại identify-service.
4. **Trên trạm (chỉ đổi cấu hình):** `servers.pallet_done_url = http://<IP WCS B>:9000/identify-service/ids/pallet_identify_done`, khởi động lại G8-ID.
5. **Trên WCS B:** bật line (tab "Trạm / Line"). Gửi một PO một pallet từ WMS ảo để chạy thử.
6. **Rollback:** tắt line trên B, rồi làm ngược bước 3–4.

Line đang tắt thì WCS B từ chối lệnh mới (201). Lệnh cho line không có trong cấu hình cũng bị từ chối, thay vì treo `Pending` như WCS gốc.

## 9. Kiểm thử

- `run_tests.bat` (`tests/e2e_test.py`) chạy 14 kịch bản đầu cuối trên cổng và DB riêng:
  1. Move nhóm 2 bước.
  2. RCS báo lỗi → SKIPPED → chạy lại.
  3. Vị trí sai / robot sai.
  4. Gửi lặp `Task_ID`.
  5. PO 2 pallet → trạm → WMS → `confirm_product` → tự cất kệ.
  6. ASN.
  7. Hủy PO.
  8. DO 2 pallet.
  9. WMS lỗi 503 rồi gửi lại.
  10. Line không thuộc B / cutover.
  11. Callback RCS của luồng A bị bỏ qua.
  12. Khởi động lại WCS khi task đang chạy, chỉ dùng kênh hỏi RCS.
  13. WMS tắt → outbox gửi bù.
  14. Ô đích đầy.
- `tests/g8_modules_test.py` test với phần mềm trạm thật ở chế độ mô phỏng (G8-Export, G8-ID). Kết quả ghi ở `docs/TEST_REPORT.md`.

## 10. Chưa làm / còn mở

- Đổi đích task đang chạy (`questionLocationNew` + `updateOrderPointInfo`), API 6 (ASN confirm PDA), API 9 (đóng lệnh SX). WCS gốc cũng chưa có hai API này.
- Đăng nhập cho giao diện web: hiện mở trong LAN, giống các tool G8 khác.
- Cần thử ở hiện trường:
  - RCS gửi callback tới một hay nhiều URL.
  - Tên điểm có dấu `-` và mỗi tầng kệ của xe nâng có tên riêng.
