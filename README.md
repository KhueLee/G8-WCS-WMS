# G8 luồng B – WMS ảo + WCS B (v0.1.0)

Hệ tích hợp tự chủ cho G8: **WMS ảo** nói API TKELog V5, **WCS B** nói đúng device API của G8-ID / G8-Export và API RCS HuaRay. Chuyển một trạm từ luồng A (TKELog + WCS gốc) sang luồng B chỉ cần đổi URL, không sửa phần mềm trạm.

- Spec: [docs/SPEC.md](docs/SPEC.md)
- Kết quả test: [docs/TEST_REPORT.md](docs/TEST_REPORT.md)
- Việc còn lại và quyết định đã chốt: [TODO.md](TODO.md)

## Cài và chạy (Windows)

1. Cài Python 3.11+ 64-bit, tick *Add python.exe to PATH*.
2. Nhấp đúp `install.bat`: tạo `.venv` và cài thư viện.
3. MongoDB: nếu máy chưa có MongoDB thì chạy `start_mongodb.bat` (mongod 4.4 kèm trong `mongodb\bin`, dữ liệu ở `data\db`).
4. Sửa `config\wcs.json` và `config\wms.json`:
   - địa chỉ RCS, trạm, WMS;
   - danh sách line;
   - file vị trí `config\locations.json` (hoặc `.xlsx` có cột `Location Code`).
5. Chạy `run_wms.bat` và `run_wcs.bat`. Mỗi file mở một cửa sổ riêng; cửa sổ tự khởi động lại chương trình nếu nó bị chết.
   - Giao diện WCS B: `http://<IP máy>:9000`
   - Giao diện WMS ảo: `http://<IP máy>:9100`
6. Trên RCS, vào *Third-party Service Configuration* và thêm một mục task-status trỏ về `http://<IP máy WCS B>:9000/rcs/taskStatusReport`.
7. Mở cổng 9000 / 9100 cho máy khác gọi vào: chuột phải `tools\open_firewall.bat` → *Run as administrator*.

**Chạy thử trên một máy không cần thiết bị:** `run_all_demo.bat` bật MongoDB, các simulator, WMS ảo và WCS B, rồi mở hai giao diện.

**Test:**
- `run_tests.bat` chạy 14 kịch bản đầu cuối trên cổng và DB riêng, không đụng dữ liệu đang chạy.
- Test với phần mềm trạm thật ở chế độ mô phỏng:
  ```
  .venv\Scripts\python tests\g8_modules_test.py --export C:\G8-Export --id C:\G8-ID\G8-ID-Deploy
  ```
  Cần cài thêm thư viện của hai phần mềm đó: `flask waitress pyserial comtypes segno websockets watchdog pywin32`.

## Cấu trúc

```
g8b/
  common.py     cấu hình, MongoDB, log, HTTP
  outbox.py     hàng đợi gửi đi (gửi lại, idempotency)
  locations.py  danh sách vị trí, chuẩn hoá mã A1-16-1
  wcs.py        WCS B
  wms.py        WMS ảo
  sim.py        simulator RCS / bàn định danh / băng tải xuất
  static/       giao diện web
config/         wcs.json, wms.json, locations.json
tests/          e2e_test.py (14 kịch bản), g8_modules_test.py (G8-Export, G8-ID thật)
docs/           SPEC.md, TEST_REPORT.md
logs/           log từng app, xoay theo ngày, giữ 30 ngày
```
