"""Tạo file hướng dẫn sử dụng docs/HUONG_DAN_SU_DUNG.pdf.

Cần hệ thống đang chạy (run.bat hoặc run.bat demo) và Microsoft Edge.
Script chụp các trang thật của site (nền sáng), ghép vào nội dung hướng dẫn rồi in PDF bằng Edge.
Chạy: .venv\\Scripts\\python tools\\build_guide.py [--site http://127.0.0.1:5510]
"""
from __future__ import annotations

import argparse
import html
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
IMG = os.path.join(DOCS, "guide_img")
EDGE = next((p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                         r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if os.path.exists(p)), None)

SHOTS = [  # (trang, tên ảnh, chiều cao cửa sổ)
    ("w_dash", "w_dash", 1000), ("c_dash", "c_dash", 1000), ("w_new_id", "w_new_id", 700),
    ("w_new_mv", "w_new_mv", 560), ("w_new_do", "w_new_do", 640), ("w_new_ul", "w_new_ul", 560),
    ("w_pallets", "w_pallets", 760), ("w_locs", "w_locs", 760), ("w_products", "w_products", 640),
    ("w_wh", "w_wh", 820), ("w_loclist", "w_loclist", 1100), ("c_moves", "c_moves", 640),
    ("c_lines", "c_lines", 520), ("c_order", "c_order", 820), ("c_cfg", "c_cfg", 1150), ("w_cfg", "w_cfg", 900),
]


def shoot(site: str):
    os.makedirs(IMG, exist_ok=True)
    for pid, name, h in SHOTS:
        out = os.path.join(IMG, name + ".png")
        subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-proxy-server", "--hide-scrollbars",
                        "--virtual-time-budget=8000", f"--window-size=1440,{h}", f"--screenshot={out}",
                        f"{site}/?theme=light#{pid}"], capture_output=True, timeout=120)
        print("chụp", name, "OK" if os.path.exists(out) else "LỖI")


def fig(name: str, cap: str) -> str:
    return f'<figure><img src="guide_img/{name}.png"><figcaption>{html.escape(cap)}</figcaption></figure>'


def body() -> str:
    return f"""
<section class="cover">
  <img class="logo" src="../g8b/static/logo.png">
  <h1>HƯỚNG DẪN SỬ DỤNG</h1>
  <h2>Hệ tích hợp luồng B: WMS ảo + WCS B</h2>
  <p>Dự án Smart Logistics G8 Home · phiên bản 0.6.0 · 25/11/2026</p>
  <p class="muted">Giao diện: <b>http://&lt;IP máy chủ&gt;:5510</b> · Tài liệu kỹ thuật: docs/SPEC.md</p>
</section>

<h1>1. Giới thiệu</h1>
<p>Luồng B gồm hai phần mềm chạy độc lập, dùng chung một giao diện web:</p>
<ul>
 <li><b>WMS ảo</b> (cổng 9100): quản lý danh mục sản phẩm, kho, vị trí; tạo lệnh định danh / luân chuyển / xuất hàng / cập nhật vị trí; lưu pallet và thùng đã định danh để xuất hàng. Nói đúng API TKELog WMS V5.</li>
 <li><b>WCS B</b> (cổng 9000): nhận lệnh từ WMS, điều khiển bàn định danh (G8-ID), băng tải xuất (G8-Export) và robot qua RCS HuaRay; báo kết quả về WMS.</li>
 <li><b>Site giao diện</b> (cổng 5510): một trang web cho cả hai phần mềm. Mở bằng Chrome / Edge.</li>
</ul>
<p>Luồng nghiệp vụ chính:</p>
<ol>
 <li>Tạo <b>lệnh định danh</b> trên WMS → WMS sinh mã thùng, chia pallet, giữ chỗ ô trống trong kho cất.</li>
 <li>Bàn định danh nhận lệnh, công nhân cân – in tem – quét từng thùng, bấm ĐỊNH DANH khi đủ pallet.</li>
 <li>WMS ghi nhận pallet xong, <b>tự gọi robot</b> cất pallet vào ô đã giữ.</li>
 <li>Tạo <b>lệnh xuất hàng</b> từ pallet trong kho → băng tải xuất quét thùng → WMS trừ tồn.</li>
</ol>

<h1>2. Khởi động</h1>
<table>
<tr><th>File</th><th>Việc</th></tr>
<tr><td>install.bat</td><td>Cài đặt và kiểm tra môi trường (Python 3.10+, thư viện, cổng, tường lửa, kết nối RCS, RAM, ổ đĩa). Chuột phải → <i>Run as administrator</i> để tự mở cổng tường lửa. Sửa hết các mục [LỖI] trước khi chạy.</td></tr>
<tr><td>run.bat</td><td>Chạy toàn bộ: MongoDB riêng (cổng 27018), WMS ảo, WCS B, site giao diện, rồi mở trình duyệt. Mỗi thành phần một cửa sổ thu nhỏ "G8B ..." trên thanh tác vụ, tự chạy lại nếu bị dừng – <b>không đóng các cửa sổ này</b>. <code>run.bat stop</code>: dừng toàn bộ; <code>run.bat status</code>: xem trạng thái; <code>run.bat demo</code>: chạy thêm robot / bàn định danh / băng tải giả để thử.</td></tr>
<tr><td>update.bat</td><td>Cập nhật bản mới từ GitHub: sao lưu và giữ nguyên cấu hình của máy, dừng – cập nhật – kiểm tra – chạy lại hệ thống.</td></tr>
</table>
<p>Mở trình duyệt vào <b>http://&lt;IP máy chủ&gt;:5510</b>. Vào thẳng cổng 9100 / 9000 cũng tự chuyển về site này.</p>
<p><b>Lần đầu cài:</b> cài Python 3.10+ 64-bit → chuột phải <b>install.bat</b> → <i>Run as administrator</i> → sửa hết mục [LỖI] → nhấp đúp <b>run.bat</b>.
<b>Hằng ngày:</b> chỉ cần <b>run.bat</b> (bấm lại khi đang chạy cũng không sao, thành phần đã chạy được bỏ qua).
<b>Cập nhật:</b> nhấp đúp <b>update.bat</b>, xem danh sách thay đổi, chọn Y; cấu hình của máy được giữ, bản cũ sao lưu ở <code>backup\\config_&lt;thời gian&gt;</code>.</p>

<h1>3. Giao diện chung</h1>
<ul>
 <li><b>Cột trái</b>: logo, các <b>nhóm chức năng</b>: Tổng quan, Đặt lệnh, Theo dõi kho (WMS), Robot &amp; trạm (WCS), Danh mục, Hệ thống. Bấm tên nhóm để sổ / thu các chức năng con. Nhãn WMS / WCS bên cạnh cho biết chức năng thuộc phần mềm nào. Nút <b>Hướng dẫn sử dụng (PDF)</b> nằm dưới cùng cột trái.</li>
 <li>Dưới menu là trạng thái kết nối: WMS ảo, WCS B, RCS (● xanh = tốt, ▲ đỏ = lỗi, ○ = chưa rõ).</li>
 <li>Các trang theo dõi tự làm mới mỗi 3 giây; trang có ô nhập (đặt lệnh, cấu hình) không tự làm mới để không mất chữ đang gõ.</li>
 <li>Mọi thao tác thay đổi dữ liệu (hủy lệnh, xoá, khoá ô…) đều hiện <b>hộp thoại</b> nêu rõ hậu quả và các nút lựa chọn; không phải gõ lệnh.</li>
</ul>
{fig("w_dash", "Tổng quan → Kho (WMS): lệnh trong ngày, tồn kho, ô trống, biểu đồ theo giờ, độ lấp đầy ô theo kho")}
{fig("c_dash", "Tổng quan → Điều khiển (WCS): task robot, pallet định danh, băng tải, kết nối RCS / WMS, cảnh báo")}

<h1>4. Đặt lệnh</h1>
<h2>4.1 Định danh</h2>
<p>Đi đôi với lệnh sản xuất. Vì luồng B không theo dõi quá trình sản xuất, người dùng tạo thẳng lệnh định danh:</p>
<ol>
 <li>Chọn <b>Loại</b>: PO (thành phẩm) hoặc ASN (hàng thương mại); <b>Line</b> (bàn định danh); <b>Sản phẩm</b>.</li>
 <li>Nhập <b>Số thùng kế hoạch</b>. Dòng dưới form tính sẵn số pallet theo quy cách pallet của sản phẩm (pallet cuối có thể lẻ).</li>
 <li>Kiểm tra <b>NSX</b>, chọn <b>Kho cất</b> (mặc định kho đánh dấu "Cất TP"). Tên lệnh để trống sẽ tự sinh dạng <b>VAS-ddmmyy-n</b>.</li>
 <li>Bấm <b>Gửi lệnh</b>. WMS sinh mã thùng, giữ chỗ ô trống cho từng pallet và gửi bàn định danh.</li>
</ol>
<p>Bảng "Lệnh định danh đang chạy" có nút <b>Hủy</b>: bàn định danh làm nốt pallet đang dở, các pallet chưa làm bị hủy và nhả ô đã giữ.</p>
{fig("w_new_id", "Đặt lệnh → Định danh")}
<h2>4.2 Luân chuyển pallet</h2>
<p>Gọi robot chuyển pallet đang trong kho sang ô trống khác. Chọn pallet, kho đích, ô đích (chỉ liệt kê ô trống), loại di chuyển (Type_Move) và robot (mặc định theo kho đích: 1 nâng hạ / 2 đội gầm).</p>
{fig("w_new_mv", "Đặt lệnh → Luân chuyển pallet")}
<h2>4.3 Xuất hàng</h2>
<p>Chọn băng tải, tick các pallet cần xuất và sửa số thùng cần xuất (mặc định hết số còn lại). Băng tải xuất quét từng thùng, đủ số thì báo về; WMS trừ thùng đã xuất, pallet hết thùng chuyển "Đã xuất hết" và ô được nhả.</p>
{fig("w_new_do", "Đặt lệnh → Xuất hàng")}
<h2>4.4 Cập nhật vị trí</h2>
<p>Dùng khi pallet được dời bằng tay hoặc kiểm kê thấy lệch: WMS ghi nhận vị trí mới, <b>không gọi robot</b>, không gửi lệnh cho WCS. Chọn "(bỏ khỏi kệ)" nếu pallet không còn ở ô nào.</p>
{fig("w_new_ul", "Đặt lệnh → Cập nhật vị trí")}
<h2>4.5 Lệnh tay / bản tin test (WCS)</h2>
<p><b>Move tay</b>: gọi robot trực tiếp từ WCS (không báo WMS), mỗi bước dạng <code>TỪ&gt;ĐẾN</code>. <b>Gửi bản tin WMS</b>: dán bản tin đúng định dạng TKELog để thử, bấm "Mẫu" để có bản tin ví dụ.</p>
{fig("c_order", "Đặt lệnh → Lệnh tay / bản tin test")}

<h1>5. Theo dõi kho (WMS)</h1>
<ul>
 <li><b>Lệnh WMS</b>: mọi lệnh và trạng thái (SENT đã gửi, RUNNING đang chạy, COMPLETED xong, FAILED / REJECTED lỗi).</li>
 <li><b>Pallet / tồn kho</b>: lọc theo kho, trạng thái, tìm mã. Nút <b>Sửa</b> mở hộp thoại chọn trạng thái và vị trí; <b>Xoá</b> nhả ô đang giữ. Form <b>Tạo pallet tay</b> dùng cho hàng có sẵn (WMS sinh mã thùng theo quy cách).</li>
 <li><b>Vị trí</b>: lưới ô kệ theo kho; <b>viền xanh lá = trống</b>, <b>viền cam = có pallet</b> (trong ô ghi mã pallet), viền xanh nét đứt = robot đang tới, nền xám = khoá; bấm một ô để xem thông tin và chọn thao tác: <b>Khoá ô</b>, <b>Mở khoá</b>, <b>Làm trống ô</b> (pallet mất vị trí), <b>Đặt pallet vào ô</b>.</li>
 <li><b>Sự kiện</b>: nhật ký nghiệp vụ.</li>
</ul>
{fig("w_pallets", "Theo dõi kho → Pallet / tồn kho")}
{fig("w_locs", "Theo dõi kho → Vị trí (bấm ô để chọn thao tác)")}

<h1>6. Robot &amp; trạm (WCS)</h1>
<ul>
 <li><b>Task robot</b>: từng bước di chuyển gửi RCS. Task đang chạy có <b>Hủy</b> / <b>Xong tay</b>; task lỗi có <b>Chạy lại</b> (chạy lại cả các bước sau trong nhóm). Cột "Báo WMS" cho biết WMS đã nhận kết quả chưa.</li>
 <li><b>Định danh tại bàn</b>: tiến độ từng pallet ở bàn định danh. <b>Xuất tại băng tải</b>: tiến độ DO, nút <b>Đóng tay</b> để kết thúc DO xuất thiếu.</li>
 <li><b>Trạm / Line</b>: bật / tắt line. Tắt line = WCS B không nhận lệnh mới cho line đó (dùng khi chuyển trạm giữa luồng A và B).</li>
 <li><b>Vị trí (WCS)</b>: trạng thái giữ chỗ phía WCS. <b>Cảnh báo</b>: lỗi gửi lệnh, bản tin bị từ chối, task chạy quá lâu.</li>
</ul>
{fig("c_moves", "Robot & trạm → Task robot")}
{fig("c_lines", "Robot & trạm → Trạm / Line")}

<h1>7. Danh mục</h1>
<h2>7.1 Sản phẩm</h2>
<p>Mã, tên, nhóm, <b>quy cách thùng</b> (chiếc/thùng), <b>quy cách pallet</b> (thùng/pallet), GW / NW kg/thùng. Bấm Sửa để nạp vào form; không xoá được sản phẩm còn tồn (bỏ tick "Dùng" để ngừng dùng).</p>
{fig("w_products", "Danh mục → Sản phẩm")}
<h2>7.2 Kho</h2>
<p>Kho chỉ để ghép điểm theo <b>tiền tố mã</b>. Một điểm thuộc kho có tiền tố khớp dài nhất (S02-… vào kho S02 trước kho S). Mỗi kho có WH_Code (gửi trong bản tin TKELog), robot mặc định (<b>kho S chỉ dùng robot đội gầm – latent</b>, các kho khác nâng hạ), và dấu "Cất TP" cho kho mặc định cất pallet định danh xong.</p>
{fig("w_wh", "Danh mục → Kho")}
<h2>7.3 Danh sách vị trí (highbay)</h2>
<ul>
 <li><b>Xuất Excel (dạng RAW)</b>: cùng bố cục file "2500 RAW.xlsx" (Node cột D, Rack G, Cột H, Tầng I, Mã K, Level Height M, Toạ độ O) kèm sheet TrangThai. <b>Xuất form 14 cột RCS</b>: dạng "Area_..._High Location.xlsx".</li>
 <li><b>Nạp file highbay mới</b> (form 14 cột hoặc RAW): 1. Đọc file → 2. Chọn kho và số điểm tối đa (mặc định 50 điểm S02 + 50 điểm C) → 3. Xem khác biệt → 4. Áp dụng. Điểm mới được thêm; <b>điểm không còn bị xoá kèm pallet gán ở điểm đó</b>; kho được tạo đủ cho mọi tiền tố trong file.</li>
 <li>Sửa / thêm một ô, <b>Thêm cả dải ô</b> (Rack, cột từ–đến, tầng từ–đến, Node tăng dần), Xoá ô (hộp thoại báo pallet bị xoá theo).</li>
</ul>
{fig("w_loclist", "Danh mục → Danh sách vị trí (highbay)")}

<h1>8. Hệ thống</h1>
<ul>
 <li><b>Outbox WMS / WCS</b>: bản tin chờ gửi; tự gửi lại tới khi bên nhận xác nhận. Bản tin DEAD (bị từ chối hẳn) có nút Gửi lại.</li>
 <li><b>Bản tin WMS / WCS</b>: mọi bản tin vào / ra kèm nội dung, dùng để đối soát.</li>
 <li><b>Cấu hình WCS</b>: địa chỉ WMS, RCS (areaId, template robot, priority, taskPath theo mã hoặc Node), danh sách bàn định danh / băng tải. <b>Cấu hình WMS</b>: địa chỉ WCS, tài khoản API, line → điểm sàn, tiền tố mã. Bấm <b>Lưu và áp dụng</b>: ghi file config (giữ bản .bak), có hiệu lực ngay.</li>
</ul>
{fig("c_cfg", "Hệ thống → Cấu hình WCS")}

<h1>9. Xử lý sự cố thường gặp</h1>
<table>
<tr><th>Hiện tượng</th><th>Cách xử lý</th></tr>
<tr><td>Cột trái báo WMS ảo / WCS B "Không kết nối"</td><td>Chạy <code>run.bat status</code>; thành phần nào tắt thì chạy lại <code>run.bat</code>; xem log trong thư mục logs.</td></tr>
<tr><td>install.bat báo cổng "đang bị chương trình khác chiếm"</td><td>Tắt chương trình đó, hoặc đổi cổng trong config\\*.json rồi chạy lại install.bat.</td></tr>
<tr><td>Giao diện chưa đổi sau khi cập nhật</td><td>Bấm Ctrl+F5 trên trình duyệt một lần.</td></tr>
<tr><td>RCS báo lỗi</td><td>Kiểm tra máy RCS (192.168.110.100:7000) và URL RCS trong Cấu hình WCS.</td></tr>
<tr><td>Lệnh định danh báo "Kho ... chỉ còn N ô trống"</td><td>Giải phóng ô hoặc nạp thêm điểm cho kho cất (Danh mục → Danh sách vị trí).</td></tr>
<tr><td>Task robot FAILED</td><td>Xem cột Lỗi; sửa nguyên nhân (ô sai, robot báo lỗi) rồi bấm Chạy lại; hoặc Xong tay nếu robot đã làm xong.</td></tr>
<tr><td>Bản tin DEAD trong Outbox</td><td>Bên nhận từ chối dữ liệu: xem Lỗi cuối, sửa rồi bấm Gửi lại.</td></tr>
<tr><td>Ô kẹt BUSY</td><td>Robot đang chở tới; nếu task đã hủy / lỗi mà ô vẫn BUSY, kiểm tra Task robot rồi chạy lại hoặc hủy task.</td></tr>
</table>

<h1>10. Chuyển một bàn định danh từ luồng A sang luồng B</h1>
<ol>
 <li>Cấu hình WCS: thêm line (Line_ID, URL http://&lt;IP trạm&gt;:8500/ids, điểm sàn). Cấu hình WMS: thêm line → điểm sàn.</li>
 <li>Chờ bàn định danh làm hết pallet đang dở; trên TKELog không gửi lệnh mới cho line này.</li>
 <li>WCS gốc: gỡ line khỏi line_manufacture_config.json, khởi động lại identify-service.</li>
 <li>Trên máy trạm G8-ID: đổi <b>pallet_done_url</b> = http://&lt;IP WCS B&gt;:9000/identify-service/ids/pallet_identify_done, khởi động lại G8-ID.</li>
 <li>Robot &amp; trạm → Trạm / Line: bật line; tạo một lệnh định danh 1 pallet để chạy thử.</li>
 <li>Quay về luồng A: tắt line trên WCS B, làm ngược bước 3–4.</li>
</ol>
"""


CSS = """
@page { size: A4; margin: 14mm 13mm; }
body { font-family: "Segoe UI", Arial, sans-serif; font-size: 10.5pt; color: #111; line-height: 1.45; }
h1 { font-size: 16pt; color: #e8541e; border-bottom: 2px solid #e8541e; padding-bottom: 3px; margin: 18px 0 8px; page-break-after: avoid; }
h1:not(:first-of-type) { page-break-before: always; }
h2 { font-size: 12.5pt; margin: 14px 0 6px; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; page-break-inside: avoid; }
th, td { border: 1px solid #bbb; padding: 4px 6px; text-align: left; vertical-align: top; }
th { background: #f3f3f3; }
figure { margin: 10px 0; page-break-inside: avoid; text-align: center; }
figure img { max-width: 100%; max-height: 235mm; border: 1px solid #ccc; }
figcaption { font-size: 9pt; color: #555; margin-top: 3px; font-style: italic; }
code { background: #f2f2f2; padding: 0 3px; }
.cover { text-align: center; padding-top: 55mm; page-break-after: always; }
.cover .logo { width: 70mm; }
.cover h1 { border: 0; color: #111; font-size: 26pt; page-break-before: avoid; }
.cover h2 { font-size: 15pt; color: #e8541e; }
.muted { color: #666; }
"""


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="http://127.0.0.1:5510")
    ap.add_argument("--no-shots", action="store_true", help="dùng ảnh đã chụp trong docs/guide_img")
    a = ap.parse_args()
    if not EDGE:
        sys.exit("Không thấy Microsoft Edge")
    if not a.no_shots:
        shoot(a.site.rstrip("/"))
    src = os.path.join(DOCS, "huong_dan.html")
    with open(src, "w", encoding="utf-8") as f:
        f.write(f'<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>Hướng dẫn sử dụng G8 luồng B</title>'
                f'<style>{CSS}</style></head><body>{body()}</body></html>')
    pdf = os.path.join(DOCS, "HUONG_DAN_SU_DUNG.pdf")
    tmp = pdf + ".tmp.pdf"
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=5000",
                    f"--print-to-pdf={tmp}", "file:///" + src.replace("\\", "/")], capture_output=True, timeout=180)
    if not os.path.exists(tmp) or os.path.getsize(tmp) < 10000:
        sys.exit("In PDF lỗi")
    shutil.move(tmp, pdf)
    print("Đã tạo", pdf, os.path.getsize(pdf) // 1024, "KB")


if __name__ == "__main__":
    main()
