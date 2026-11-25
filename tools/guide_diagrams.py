"""Sơ đồ SVG cho hướng dẫn sử dụng (in ra PDF, nền sáng).

workflow_svg(): quy trình vận hành của nhân công - làn theo vai trò, bước nối nhau từ trên xuống.
sequence_svg(): luồng vận hành giữa người, WMS ảo, WCS B, bàn định danh, RCS / robot, kệ - theo thứ tự bản tin.
Sửa nội dung ở hai danh sách STEPS / MSGS; toạ độ tự tính.
"""
from __future__ import annotations

from html import escape

INK, INK2, MUTED, LINE = "#111111", "#444444", "#777777", "#9a9a9a"
ORANGE, ORANGE_BG = "#e8541e", "#fdeee6"
BLUE, BLUE_BG = "#2a78d6", "#e8f1fc"
GREEN, GREEN_BG = "#14865c", "#e6f5ee"
GREY_BG = "#f4f4f2"

ARROW = (f'<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
         f'<path d="M0,0 L10,5 L0,10 z" fill="{INK2}"/></marker>'
         f'<marker id="ahb" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
         f'<path d="M0,0 L10,5 L0,10 z" fill="{BLUE}"/></marker></defs>')


def _text(x, y, lines, size=12, weight=400, color=INK, anchor="middle", lh=15):
    t = "".join(f'<tspan x="{x}" dy="{0 if i == 0 else lh}">{escape(s)}</tspan>' for i, s in enumerate(lines))
    return f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{color}" text-anchor="{anchor}">{t}</text>'


# ---------------------------------------------------------------------- 1. quy trình vận hành của nhân công
LANES = [("Kế hoạch sản xuất", GREY_BG), ("Người vận hành", ORANGE_BG), ("Công nhân bàn định danh", GREEN_BG),
         ("Hệ thống tự động", BLUE_BG)]
LANE_SUB = ["(văn phòng)", "(site :5510)", "(màn hình G8-ID)", "(WMS · WCS · robot)"]
# (làn, các dòng chữ, kiểu): kiểu "auto" = hệ thống tự làm, "loop" = lặp mỗi thùng
STEPS = [
    (0, ["1. Xuất file lệnh sản xuất", "từ hệ thống G8", "(CKHC_G8_San_Xuat_Export)"], ""),
    (1, ["2. Lệnh sản xuất → Đọc file", "chọn Kế hoạch / Đang SX", "→ lệnh Chờ định danh"], ""),
    (1, ["3. Danh mục → Sản phẩm:", "kiểm tra quy cách thùng,", "quy cách pallet, GW"], ""),
    (1, ["4. Chuyền bắt đầu đóng thùng:", "bấm Định danh trên lệnh", "(bàn, số thùng, NSX, kho cất)"], ""),
    (3, ["5. WMS sinh mã thùng, chia", "pallet, giữ chỗ ô cất; WCS", "gửi lệnh xuống bàn định danh"], "auto"),
    (2, ["6. Bấm BẮT ĐẦU trên G8-ID", "(nếu trạm đang dừng)"], ""),
    (2, ["7. Đặt thùng lên cân → in tem", "→ dán tem → quét mã", "(lặp từng thùng)"], "loop"),
    (2, ["8. Đủ thùng: bấm ĐỊNH DANH,", "để pallet ở điểm sàn của bàn"], ""),
    (3, ["9. WMS trả ô cất; robot tự lấy", "pallet ở điểm sàn, cất lên kệ"], "auto"),
    (1, ["10. Theo dõi cột Tiến độ:", "thùng / pallet đã định danh,", "pallet đã cất"], ""),
    (3, ["11. Đủ pallet → lệnh Hoàn thành;", "pallet Trong kho, sẵn sàng xuất"], "auto"),
    (1, ["12. Cuối ca: Xuất file lệnh", "(theo bộ lọc) để đối chiếu"], ""),
]


def workflow_svg() -> str:
    lw, top, bw, bh, gap = 190, 58, 170, 56, 22
    W = lw * len(LANES)
    H = top + len(STEPS) * (bh + gap) + 20
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="100%" font-family="Segoe UI, Arial, sans-serif" role="img" '
           f'aria-label="Quy trình vận hành của nhân công">', ARROW]
    for i, (name, bg) in enumerate(LANES):
        out.append(f'<rect x="{i * lw}" y="0" width="{lw}" height="{H}" fill="{bg}" stroke="#ffffff" stroke-width="3"/>')
        out.append(_text(i * lw + lw / 2, 22, [name], 12.5, 700))
        out.append(_text(i * lw + lw / 2, 39, [LANE_SUB[i]], 10.5, 400, MUTED))
    pos = []
    for k, (lane, lines, kind) in enumerate(STEPS):
        x, y = lane * lw + (lw - bw) / 2, top + k * (bh + gap)
        pos.append((x, y))
        stroke = {"auto": BLUE, "loop": GREEN}.get(kind, ORANGE)
        dash = ' stroke-dasharray="5 3"' if kind == "auto" else ""
        out.append(f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="7" fill="#ffffff" stroke="{stroke}" stroke-width="1.6"{dash}/>')
        n = len(lines)
        out.append(_text(x + bw / 2, y + bh / 2 - (n - 1) * 7.5 + 4, lines, 11, 400, INK, lh=15))
    for k in range(len(STEPS) - 1):        # mũi tên bước k -> k+1: xuống, ngang, xuống
        (x1, y1), (x2, y2) = pos[k], pos[k + 1]
        ax, ay, bx, by = x1 + bw / 2, y1 + bh, x2 + bw / 2, y2
        my = (ay + by) / 2
        d = f"M{ax},{ay} L{ax},{my} L{bx},{my} L{bx},{by - 1}" if ax != bx else f"M{ax},{ay} L{bx},{by - 1}"
        out.append(f'<path d="{d}" fill="none" stroke="{INK2}" stroke-width="1.3" marker-end="url(#ah)"/>')
    # vòng lặp: sau bước 8 quay lại bước 7 cho pallet tiếp theo
    (x7, y7), (x8, y8) = pos[6], pos[7]
    rx = x8 + bw + 8
    out.append(f'<path d="M{x8 + bw},{y8 + bh / 2} L{rx},{y8 + bh / 2} L{rx},{y7 + bh / 2} L{x7 + bw + 1},{y7 + bh / 2}" fill="none" '
               f'stroke="{GREEN}" stroke-width="1.3" stroke-dasharray="4 3" marker-end="url(#ah)"/>')
    out.append(_text(rx - 4, (y7 + y8 + bh) / 2 + 4, ["pallet", "tiếp"], 9.5, 600, GREEN, "end", 11))
    # chú thích
    ly = H - 6
    out.append(f'<rect x="8" y="{ly - 11}" width="16" height="10" rx="2" fill="#fff" stroke="{ORANGE}"/>' + _text(30, ly - 2, ["người làm"], 10, 400, INK2, "start"))
    out.append(f'<rect x="110" y="{ly - 11}" width="16" height="10" rx="2" fill="#fff" stroke="{BLUE}" stroke-dasharray="4 2"/>' + _text(132, ly - 2, ["hệ thống tự làm"], 10, 400, INK2, "start"))
    out.append(f'<rect x="240" y="{ly - 11}" width="16" height="10" rx="2" fill="#fff" stroke="{GREEN}"/>' + _text(262, ly - 2, ["lặp từng thùng / pallet"], 10, 400, INK2, "start"))
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------------- 2. sơ đồ luồng vận hành (bản tin)
ACTORS = [("Người vận hành", "site :5510", ORANGE), ("WMS ảo", ":9100", BLUE), ("WCS B", ":9000", BLUE),
          ("Bàn định danh", "G8-ID + công nhân", GREEN), ("RCS + robot", "HuaRay", INK2), ("Kệ kho", "ô cất", INK2)]
P, WMS, WCS, IDS, RCS, RACK = range(6)
# (từ, tới, chữ, kiểu): từ == tới -> việc nội bộ; kiểu "ret" = phản hồi / báo về
MSGS = [
    (P, WMS, "Nạp file lệnh sản xuất → Chờ định danh", ""),
    (P, WMS, "Bấm Định danh (bàn, số thùng, NSX, kho cất)", ""),
    (WMS, WMS, "Sinh mã thùng, chia pallet, giữ chỗ ô cất", ""),
    (WMS, WCS, "identify-cartons: PO, pallet, Carton_List, ô cất", ""),
    (WCS, IDS, "orderTask: mỗi pallet một lệnh, gửi ngay", ""),
    (IDS, IDS, "Cân – in tem – quét từng thùng", ""),
    (IDS, WCS, "pallet_identify_done (đủ pallet)", "ret"),
    (WCS, WMS, "Finished_Goods → WMS trả ô cất, tiến độ +1 pallet", "ret"),
    (WCS, IDS, "confirm_product (vị trí cất)", ""),
    (WMS, WCS, "move_pallet: điểm sàn → ô cất", ""),
    (WCS, RCS, "addTask (taskPath: điểm sàn, ô)", ""),
    (RCS, RACK, "Robot lấy pallet, đặt vào ô", ""),
    (RCS, WCS, "taskStatusReport (callback: xong)", "ret"),
    (WCS, WMS, "Pallet_Location → pallet Trong kho", "ret"),
    (WMS, P, "Tiến độ x/y pallet; đủ pallet → Hoàn thành", "ret"),
]
LOOP = (5, 13)   # các bản tin lặp cho mỗi pallet (chỉ số trong MSGS)


def sequence_svg() -> str:
    cw, left, top, row = 128, 14, 64, 40
    xs = [left + cw / 2 + i * cw for i in range(len(ACTORS))]
    W = left * 2 + cw * len(ACTORS)
    H = top + len(MSGS) * row + 30
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="100%" font-family="Segoe UI, Arial, sans-serif" role="img" '
           f'aria-label="Sơ đồ luồng vận hành">', ARROW]
    # khung lặp mỗi pallet
    y0, y1 = top + LOOP[0] * row + 4, top + (LOOP[1] + 1) * row - 6
    out.append(f'<rect x="{xs[1] - 50}" y="{y0}" width="{xs[5] - xs[1] + 100}" height="{y1 - y0}" rx="6" fill="#fafafa" stroke="{LINE}" stroke-dasharray="5 3"/>')
    out.append(f'<rect x="{xs[1] - 50}" y="{y0}" width="118" height="17" rx="6" fill="{LINE}"/>' + _text(xs[1] - 44, y0 + 12.5, ["lặp mỗi pallet"], 10, 700, "#ffffff", "start"))
    for x, (name, sub, color) in zip(xs, ACTORS):
        out.append(f'<line x1="{x}" y1="{top - 10}" x2="{x}" y2="{H - 8}" stroke="{LINE}" stroke-width="1" stroke-dasharray="3 3"/>')
        out.append(f'<rect x="{x - 58}" y="6" width="116" height="42" rx="7" fill="#ffffff" stroke="{color}" stroke-width="1.8"/>')
        out.append(_text(x, 24, [name], 11.5, 700) + _text(x, 39, [sub], 9.5, 400, MUTED))
    for k, (a, b, label, kind) in enumerate(MSGS):
        y = top + k * row + 22
        num = f"{k + 1}. "
        if a == b:
            out.append(f'<rect x="{xs[a] - 60}" y="{y - 14}" width="120" height="26" rx="5" fill="{GREEN_BG if a == IDS else BLUE_BG}" stroke="none"/>')
            words, lines, cur = (num + label).split(), [], ""
            for w in words:
                if len(cur) + len(w) > 24:
                    lines.append(cur.strip())
                    cur = ""
                cur += w + " "
            lines.append(cur.strip())
            out.append(_text(xs[a], y - 3 - (len(lines) - 1) * 5, lines, 9.5, 400, INK, lh=11))
            continue
        x1, x2 = xs[a], xs[b]
        color = BLUE if kind == "ret" else INK2
        dash = ' stroke-dasharray="6 3"' if kind == "ret" else ""
        end = x2 - 2 if x2 > x1 else x2 + 2
        out.append(f'<line x1="{x1}" y1="{y + 6}" x2="{end}" y2="{y + 6}" stroke="{color}" stroke-width="1.4"{dash} '
                   f'marker-end="url(#{"ahb" if kind == "ret" else "ah"})"/>')
        mid = (x1 + x2) / 2
        out.append(f'<text x="{mid}" y="{y}" font-size="10" text-anchor="middle" fill="{INK}" paint-order="stroke" stroke="#ffffff" stroke-width="3">'
                   f'<tspan font-weight="700">{escape(num)}</tspan>{escape(label)}</text>')
    out.append("</svg>")
    return "".join(out)


if __name__ == "__main__":   # xem nhanh: python tools/guide_diagrams.py > a.html
    print(f"<html><body style='max-width:820px'>{workflow_svg()}<hr>{sequence_svg()}</body></html>")
