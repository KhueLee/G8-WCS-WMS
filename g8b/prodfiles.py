"""Đọc / ghi file Excel danh mục sản phẩm và lệnh sản xuất theo đúng định dạng xuất từ hệ thống G8.

Danh sách sản phẩm ("CKHC_G8_BOM_Linh_Kien_Chuyen_San_Xuat_Export_*.xlsx"):
  dòng 1 tiêu đề: Mã BOM | Tên BOM | Chuyền ; mỗi dòng một cặp (mã, chuyền) - một mã có thể nhiều dòng.
  File xuất ra giữ nguyên 3 cột đó ở đầu, thêm các cột quy cách phía sau (đọc lại được, file gốc không có thì bỏ qua).
Lệnh sản xuất ("CKHC_G8_San_Xuat_Export_*.xlsx"):
  dòng 1 nhóm cột: H = "Thành phẩm", L = "Nguyên phụ liệu"; dòng 2 tiêu đề 17 cột; dữ liệu từ dòng 3.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

HEAD_FONT = Font(bold=True)
HEAD_FILL = PatternFill("solid", fgColor="F2F2F2")

# ---------------------------------------------------------------------- sản phẩm
PROD_COLS = ["Mã BOM", "Tên BOM", "Chuyền"]
PROD_EXTRA = [("inner_carton", "Quy cách thùng (chiếc/thùng)"), ("inner_pallet", "Quy cách pallet (thùng/pallet)"),
              ("gw", "GW (kg/thùng)"), ("nw", "NW (kg/thùng)"), ("group", "Nhóm"), ("active", "Dùng")]

# ---------------------------------------------------------------------- lệnh sản xuất
# (khoá, tiêu đề dòng 2); "Số Kiện" / "SL Chẵn" / "SL Lẻ" / "Tổng SL" xuất hiện 2 lần: lần đầu thành phẩm, lần sau NPL
ORDER_COLS = [("code", "Lệnh sản xuất"), ("wh_in", "Kho Nhập TP"), ("wh_out", "Kho xuất"), ("prod_date", "Ngày Sản Xuất"),
              ("line", "Line"), ("status_text", "Trạng thái"), ("type", "Loại hình SX"),
              ("ctn", "Số Kiện"), ("qty_even", "SL Chẵn"), ("qty_odd", "SL Lẻ"), ("qty_total", "Tổng SL"),
              ("npl_ctn", "Số Kiện"), ("npl_even", "SL Chẵn"), ("npl_odd", "SL Lẻ"), ("npl_total", "Tổng SL"),
              ("item_code", "Mã SP Group"), ("note", "Ghi Chú")]
ORDER_GROUPS = {8: "Thành phẩm", 12: "Nguyên phụ liệu"}    # cột H, L (đếm từ 1)
ORDER_WIDTHS = [17.1, 14.3, 14.3, 14.3, 14.3, 12.9, 14.3] + [11.4] * 8 + [28.6, 28.6]
NUM_KEYS = ("ctn", "qty_even", "qty_odd", "qty_total", "npl_ctn", "npl_even", "npl_odd", "npl_total")

# trạng thái trong file -> trạng thái của B; trạng thái chờ nhập vào là WAITING
STATUS_IN = {"kế hoạch": "WAITING", "đang sản xuất": "WAITING", "chờ": "WAITING", "chờ định danh": "WAITING",
             "đang định danh": "IDENTIFYING", "kết thúc": "DONE", "hoàn thành": "DONE", "đã hủy": "CANCELLED"}
STATUS_OUT = {"WAITING": "Kế hoạch", "IDENTIFYING": "Đang định danh", "DONE": "Hoàn thành", "CANCELLED": "Đã hủy"}


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _key(v) -> str:
    return re.sub(r"\s+", " ", _s(v)).lower()


def line_id(raw) -> str:
    """'MCSX005(40W)' -> 'MCSX005'; 'LEDK001' -> 'LEDK001'."""
    m = re.match(r"\s*([A-Za-z]+\d+)", _s(raw))
    return m.group(1).upper() if m else _s(raw).upper()


def carton_spec_from_name(name: str) -> int:
    """'... (48c/T)' / '(24c/T hoặc 48c/T)' -> 48 / 24 (số đầu tiên); không có thì 0."""
    m = re.search(r"(\d+)\s*c\s*/\s*t\b", _s(name), re.I)
    return int(m.group(1)) if m else 0


def fmt_date(v) -> str:
    if isinstance(v, (datetime, date)):
        return v.strftime("%d/%m/%Y")
    s = _s(v)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else s


def _num(v) -> float:
    try:
        f = float(v)
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return 0


def _rows(raw: bytes):
    wb = load_workbook(io.BytesIO(raw), data_only=True)
    return list(wb.worksheets[0].iter_rows(values_only=True))


def _find_header(rows, first: str, scan: int = 6) -> int:
    for i, r in enumerate(rows[:scan]):
        if r and _key(r[0]) == _key(first):
            return i
    raise ValueError(f'Không thấy dòng tiêu đề bắt đầu bằng "{first}" - file không đúng định dạng')


def _style_header(ws, row: int, ncol: int):
    for c in range(1, ncol + 1):
        cell = ws.cell(row=row, column=c)
        cell.font, cell.fill = HEAD_FONT, HEAD_FILL


def read_products(raw: bytes) -> list[dict]:
    """-> [{item_code, item_name, lines:[chuyền gốc], (các cột quy cách nếu có)}], gộp các dòng cùng mã."""
    rows = _rows(raw)
    h = _find_header(rows, PROD_COLS[0])
    head = [_key(x) for x in rows[h]]
    idx = {k: head.index(_key(t)) for k, t in zip(("item_code", "item_name", "line"), PROD_COLS) if _key(t) in head}
    if len(idx) < 3:
        raise ValueError("File sản phẩm cần 3 cột: " + ", ".join(PROD_COLS))
    extra = {k: head.index(_key(t)) for k, t in PROD_EXTRA if _key(t) in head}
    out: dict[str, dict] = {}
    for r in rows[h + 1:]:
        code = _s(r[idx["item_code"]] if idx["item_code"] < len(r) else "")
        if not code:
            continue
        p = out.setdefault(code, {"item_code": code, "item_name": "", "lines": []})
        name = _s(r[idx["item_name"]])
        if name and not p["item_name"]:
            p["item_name"] = name
        ln = _s(r[idx["line"]])
        if ln and ln not in p["lines"]:
            p["lines"].append(ln)
        for k, i in extra.items():
            v = r[i] if i < len(r) else None
            if v not in (None, "") and k not in p:
                p[k] = _s(v) if k == "group" else (_s(v).lower() not in ("0", "false", "không", "khong", "")
                                                   if k == "active" else _num(v))
    return list(out.values())


def write_products(products: list[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(PROD_COLS + [t for _, t in PROD_EXTRA])
    _style_header(ws, 1, len(PROD_COLS) + len(PROD_EXTRA))
    for p in products:
        tail = [p.get("inner_carton") or None, p.get("inner_pallet") or None, p.get("gw") or None, p.get("nw") or None,
                p.get("group") or None, 1 if p.get("active", True) else 0]
        for ln in (p.get("lines") or [None]):
            ws.append([p["item_code"], p.get("item_name", ""), ln] + tail)
    for col, w in zip("ABCDEFGHI", (24, 70, 16, 14, 14, 12, 12, 14, 8)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def read_orders(raw: bytes) -> list[dict]:
    """-> [{code, line, line_id, prod_date dd/mm/yyyy, status_text, ..., item_code, note}] (dòng trống bị bỏ)."""
    rows = _rows(raw)
    h = _find_header(rows, ORDER_COLS[0][1])
    head = [_key(x) for x in rows[h]]
    pos, used = {}, set()
    for k, t in ORDER_COLS:          # tiêu đề trùng tên: lấy lần xuất hiện kế tiếp chưa dùng
        i = next((j for j, x in enumerate(head) if x == _key(t) and j not in used), None)
        if i is None:
            raise ValueError(f'File lệnh sản xuất thiếu cột "{t}"')
        pos[k] = i
        used.add(i)
    out = []
    for r in rows[h + 1:]:
        v = {k: (r[i] if i < len(r) else None) for k, i in pos.items()}
        if not _s(v["code"]):
            continue
        o = {k: _s(x) for k, x in v.items()}
        o["prod_date"] = fmt_date(v["prod_date"])
        for k in NUM_KEYS:
            o[k] = _num(v[k])
        o["line_id"] = line_id(o["line"])
        out.append(o)
    return out


def write_orders(orders: list[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for c, t in ORDER_GROUPS.items():
        ws.cell(row=1, column=c, value=t)
    for c, (_, t) in enumerate(ORDER_COLS, 1):
        ws.cell(row=2, column=c, value=t)
    _style_header(ws, 1, len(ORDER_COLS))
    _style_header(ws, 2, len(ORDER_COLS))
    for n, o in enumerate(orders, 3):
        for c, (k, _) in enumerate(ORDER_COLS, 1):
            v = o.get(k, "")
            if k == "status_text":
                v = STATUS_OUT.get(o.get("status", ""), o.get("status_text", ""))
            elif k == "prod_date":
                try:
                    v = datetime.strptime(_s(v), "%d/%m/%Y")
                except ValueError:
                    pass
            elif k in NUM_KEYS:
                v = _num(v)
            cell = ws.cell(row=n, column=c, value=v)
            if k == "prod_date" and isinstance(v, datetime):
                cell.number_format = "dd/mm/yyyy"
    for i, w in enumerate(ORDER_WIDTHS):
        ws.column_dimensions[chr(65 + i)].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
