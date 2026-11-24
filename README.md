# G8 luồng B – WMS ảo + WCS B (v0.5.0)

Hệ tích hợp tự chủ cho G8: **WMS ảo** nói API TKELog V5, **WCS B** nói đúng device API của G8-ID / G8-Export và API RCS HuaRay. Chuyển một trạm từ luồng A (TKELog + WCS gốc) sang luồng B chỉ cần đổi URL, không sửa phần mềm trạm.

- Spec: [docs/SPEC.md](docs/SPEC.md)
- Kết quả test: [docs/TEST_REPORT.md](docs/TEST_REPORT.md)
- Việc còn lại và quyết định đã chốt: [TODO.md](TODO.md)

## Cài và chạy (Windows)

1. Cài Python 3.11+ 64-bit, tick *Add python.exe to PATH*.
2. Nhấp đúp `install.bat`: tạo `.venv` và cài thư viện.
3. MongoDB: chạy `start_mongodb.bat` để có MongoDB **riêng của luồng B ở cổng 27018** (mongod 4.4 kèm trong `mongodb\bin`, dữ liệu ở `data\db`).
   Cổng 27018 tách hẳn khỏi MongoDB khác trên máy (WCS gốc, G8-ID dùng 27017), nên hai bên không ảnh hưởng nhau.
4. Sửa cấu hình (trên web, tab Cấu hình, hoặc trong `config\wcs.json` / `config\wms.json`):
   - địa chỉ RCS, trạm, WMS;
   - danh sách line;
   - danh sách vị trí `config\locations.xlsx` **theo form highbay của RCS** (14 cột Area Name, Location Code, Node, …), sửa được trên web.
5. Chạy `run_wms.bat`, `run_wcs.bat` và `run_web.bat`. Mỗi file mở một cửa sổ riêng; cửa sổ tự khởi động lại chương trình nếu nó bị chết.
   - **Giao diện chung: `http://<IP máy>:9200`** (cả WMS ảo và WCS B; vào :9000 / :9100 cũng tự chuyển về đây).
   - Nút **Hướng dẫn sử dụng (PDF)** ở cột trái mở `docs/HUONG_DAN_SU_DUNG.pdf`.
6. Trên RCS, vào *Third-party Service Configuration* và thêm một mục task-status trỏ về `http://<IP máy WCS B>:9000/rcs/taskStatusReport`.
7. Mở cổng 9000 / 9100 / 9200 cho máy khác gọi vào: chuột phải `tools\open_firewall.bat` → *Run as administrator*.

**Giao diện web** (cột trái gồm các nhóm lớn: Tổng quan, Đặt lệnh, Theo dõi kho, Robot & trạm, Danh mục, Hệ thống; bấm nhóm để sổ chức năng con; mở thẳng một trang bằng `#tên`, vd. `http://<máy>:9200/#c_cfg`; sửa ô / pallet qua hộp thoại có nút lựa chọn):
- WCS B:
  - Dashboard;
  - Di chuyển, Định danh, Xuất hàng;
  - **Đặt lệnh** (Move tay không báo WMS, gửi bản tin WMS mẫu để test);
  - Trạm / Line, Vị trí, Outbox, Cảnh báo, Bản tin;
  - **Danh sách vị trí (highbay)**: sửa / thêm / thêm cả dải / xoá, tải file highbay lên, **Xuất Excel dạng RAW** (hoặc form 14 cột);
  - **Cấu hình** (WMS, RCS, line định danh / băng tải, zone lưu trữ, taskPath theo mã hoặc Node).
- WMS ảo:
  - Dashboard;
  - **Đặt lệnh**: Định danh (sinh mã thùng theo quy cách sản phẩm), Luân chuyển pallet, Xuất hàng, Cập nhật vị trí (tay);
  - Theo dõi: Lệnh, Pallet / tồn kho (tạo / sửa / xoá pallet tay), Ô kệ (khoá / làm trống), Sự kiện;
  - **Danh mục**: Sản phẩm (quy cách thùng / pallet), Kho (ghép điểm theo tiền tố), Danh sách vị trí (nạp file highbay hoặc RAW, chọn kho / số điểm, xem khác biệt);
  - **Cấu hình** (WCS, tài khoản API, line → điểm sàn, zone lưu trữ, tự cất kệ).
- Lưu cấu hình trên web sẽ ghi `config/*.json` (giữ bản `.bak`) và áp dụng ngay. Riêng cổng và MongoDB thì phải sửa file rồi chạy lại.

**Chạy thử trên một máy không cần thiết bị:** `run_all_demo.bat` bật MongoDB, các simulator, WMS ảo và WCS B, rồi mở hai giao diện.

**Test:**
- `run_tests.bat` chạy 21 kịch bản đầu cuối trên cổng và DB riêng, không đụng dữ liệu đang chạy.
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
  web.py        site giao diện chung :9200 (chuyển tiếp /wms, /wcs)
  sim.py        simulator RCS / bàn định danh / băng tải xuất
  static/       giao diện web
config/         wcs.json, wms.json, web.json, warehouses.json (kho), locations.xlsx (vị trí form highbay)
tests/          e2e_test.py (21 kịch bản), g8_modules_test.py (G8-Export, G8-ID thật)
docs/           SPEC.md, TEST_REPORT.md, HUONG_DAN_SU_DUNG.pdf (+ guide_img/, tạo bằng tools/build_guide.py)
logs/           log từng app, xoay theo ngày, giữ 30 ngày
```
