# G8 luồng B – WMS ảo + WCS B (v0.6.0)

Hệ tích hợp tự chủ cho G8: **WMS ảo** nói API TKELog V5, **WCS B** nói đúng device API của G8-ID / G8-Export và API RCS HuaRay. Chuyển một trạm từ luồng A (TKELog + WCS gốc) sang luồng B chỉ cần đổi URL, không sửa phần mềm trạm.

- Spec: [docs/SPEC.md](docs/SPEC.md)
- Kết quả test: [docs/TEST_REPORT.md](docs/TEST_REPORT.md)
- Việc còn lại và quyết định đã chốt: [TODO.md](TODO.md)

## Cài và chạy (Windows)

Chỉ có **3 file** ở thư mục gốc:

| File | Việc |
|---|---|
| `install.bat` | Cài đặt và **kiểm tra môi trường**: Python 3.11+ 64-bit, `.venv` + thư viện, mongod, file cấu hình, cổng (trống / bị chiếm), tường lửa, kết nối RCS, Git, RAM, ổ đĩa. Chạy bằng *Run as administrator* thì tự mở cổng tường lửa cho WMS / WCS / site. Chạy lại bao nhiêu lần cũng được. |
| `run.bat` | **Chạy hệ thống**: MongoDB riêng (cổng 27018, cache tối đa 1 GB) → WMS ảo → WCS B → site, rồi mở giao diện. Mỗi thành phần một cửa sổ thu nhỏ "G8B ...", tự chạy lại nếu bị dừng. Thành phần nào đang chạy thì bỏ qua. `run.bat stop` dừng toàn bộ (MongoDB tắt êm), `run.bat status` xem trạng thái, `run.bat demo` chạy thêm robot / bàn định danh / băng tải giả. |
| `update.bat` | **Cập nhật** lên bản mới trên GitHub (máy cài bằng `git clone`, cần Git): hiện danh sách thay đổi, hỏi xác nhận, sao lưu `config\` vào `backup\config_<thời gian>`, dừng hệ thống, lấy code mới **giữ nguyên cấu hình của máy**, chạy `install.bat`, rồi chạy lại hệ thống nếu trước đó đang chạy. Dữ liệu MongoDB (`data\db`) không bị đụng tới; code sửa tay trên máy bị ghi đè (trừ `config\`). |

Các bước:
1. Cài Python 3.11+ 64-bit, tick *Add python.exe to PATH*.
2. Chuột phải `install.bat` → *Run as administrator*. Sửa hết các mục `[LỖI]` nếu có.
3. Sửa cấu hình (trên web, mục Hệ thống → Cấu hình, hoặc trong `config\wcs.json` / `config\wms.json`):
   - địa chỉ RCS, trạm, WMS;
   - danh sách line;
   - danh sách vị trí `config\locations.xlsx` **theo form highbay của RCS** (14 cột Area Name, Location Code, Node, …), sửa được trên web.
4. Nhấp đúp `run.bat`.
   - **Giao diện chung: `http://<IP máy>:5510`** (cả WMS ảo và WCS B; vào :9000 / :9100 cũng tự chuyển về đây).
   - Nút **Hướng dẫn sử dụng (PDF)** ở cuối cột trái mở `docs/HUONG_DAN_SU_DUNG.pdf`.
   - MongoDB của B ở cổng 27018, tách hẳn khỏi MongoDB 27017 của WCS gốc / G8-ID, hai bên không ảnh hưởng nhau.
5. Trên RCS, vào *Third-party Service Configuration* và thêm một mục task-status trỏ về `http://<IP máy WCS B>:9000/rcs/taskStatusReport`.

**Giao diện web** (cột trái gồm các nhóm lớn: Tổng quan, Đặt lệnh, Theo dõi kho, Robot & trạm, Danh mục, Hệ thống; bấm nhóm để sổ chức năng con; mở thẳng một trang bằng `#tên`, vd. `http://<máy>:5510/#c_cfg`; sửa ô / pallet qua hộp thoại có nút lựa chọn):
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
  - Theo dõi: Lệnh, Pallet / tồn kho (tạo / sửa / xoá pallet tay), Vị trí (khoá / làm trống ô), Sự kiện;
  - **Danh mục**: Sản phẩm (quy cách thùng / pallet), Kho (ghép điểm theo tiền tố), Danh sách vị trí (nạp file highbay hoặc RAW, chọn kho / số điểm, xem khác biệt);
  - **Cấu hình** (WCS, tài khoản API, line → điểm sàn, zone lưu trữ, tự cất kệ).
- Lưu cấu hình trên web sẽ ghi `config/*.json` (giữ bản `.bak`) và áp dụng ngay. Riêng cổng và MongoDB thì phải sửa file rồi chạy lại.

**Chạy thử trên một máy không cần thiết bị:** `run.bat demo` (hoặc nhấp đúp `tools\run_demo.bat`) bật thêm RCS / bàn định danh / băng tải giả.

**Test:**
- `tools\run_tests.bat` chạy 21 kịch bản đầu cuối trên cổng và DB riêng, không đụng dữ liệu đang chạy.
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
  web.py        site giao diện chung :5510 (chuyển tiếp /wms, /wcs)
  ctl.py        kiểm tra môi trường / trạng thái / dừng (dùng trong install.bat, run.bat, update.bat)
  sim.py        simulator RCS / bàn định danh / băng tải xuất
  static/       giao diện web
config/         wcs.json, wms.json, web.json, warehouses.json (kho), locations.xlsx (vị trí form highbay)
tests/          e2e_test.py (21 kịch bản), g8_modules_test.py (G8-Export, G8-ID thật)
docs/           SPEC.md, TEST_REPORT.md, HUONG_DAN_SU_DUNG.pdf (+ guide_img/, tạo bằng tools/build_guide.py)
tools/          run_demo.bat, run_tests.bat, build_guide.py
logs/           log từng app, xoay theo ngày, giữ 30 ngày
```
