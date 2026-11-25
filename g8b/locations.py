"""Vị trí và kho (dùng chung cho WMS ảo và WCS B).

- File vị trí `config/locations.xlsx` theo form highbay của RCS HuaRay (14 cột "Area_..._High Location.xlsx").
  Nạp vào được cả form 14 cột lẫn bố cục RAW (file "2500 RAW.xlsx": không tiêu đề, Node cột D, Rack G, Cột H,
  Tầng I, Mã K, Level Height M, Toạ độ O).
- Kho chỉ để ghép điểm: mỗi kho có một hoặc vài tiền tố mã điểm (vd. kho C = C24…C31, kho S02 = S02-…).
  Một điểm thuộc kho có tiền tố khớp **dài nhất**. Danh mục kho nằm ở `config/warehouses.json`, dùng chung.
- Nạp file mới: chọn kho / giới hạn số điểm cần quản lý, xem trước khác biệt so với bộ cũ rồi mới áp dụng.
  Điểm bị bỏ thì xoá luôn, kèm các đối tượng gán với điểm đó (pallet ở WMS) qua `on_removed`.
- Mã chuẩn hoá dạng `A1-16-1` (dấu `-`, chữ hoa); tên điểm trên RCS trùng mã nên WCS gửi thẳng mã vào taskPath.
- Trạng thái ô (EMPTY / FULL / BUSY / LOCKED) nằm trong MongoDB của từng app, nạp lại file không làm mất.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time

from fastapi import Request
from fastapi.responses import Response

from .common import ROOT, now

EMPTY, FULL, BUSY, LOCKED = "EMPTY", "FULL", "BUSY", "LOCKED"
STATION_ZONE = "STATION"
NO_WH = "KHAC"

# 14 cột đúng thứ tự file highbay RCS (key nội bộ, tiêu đề cột)
HIGHBAY = [
    ("area", "Area Name"), ("code", "Location Code"), ("node", "Node"), ("carrier_model", "Storage Carrier Model"),
    ("length", "Location length (mm)"), ("width", "Location width (mm)"), ("coord", "Location Coordinates"),
    ("orientation", "Location orientation(°)"), ("layer", "Location layer"), ("fork_height", "Fork lifting height"),
    ("business_type", "Business type"), ("hl_model", "High level storage model"), ("level_height", "Level Height"),
    ("self_reg", "Self regulating fork teeth of the device"),
]
HB_KEYS = [k for k, _ in HIGHBAY]
DEFAULT_ROW = {"area": "G8_NVL_SLAM", "carrier_model": None, "length": "1440.0", "width": "1440.0", "orientation": "0",
               "layer": 1, "fork_height": 0, "business_type": "Forklift", "hl_model": None, "self_reg": "false"}
LEVEL_HEIGHTS = [280, 1620, 2920, 4270]   # chiều cao tầng 1..4 trong file highbay hiện tại
# Bố cục RAW (0-based): D=Node, G=Rack, H=Cột, I=Tầng, K=Mã, M=Level Height, O=Toạ độ
RAW_COLS = {"node": 3, "rack": 6, "col": 7, "level": 8, "code": 10, "level_height": 12, "coord": 14}
# tên kho biết trước theo tiền tố (sửa được trên giao diện)
KNOWN_WH = {"C": "Kho thành phẩm", "S02": "Kho lắp ráp LED trụ", "D": "Kho 2500"}
# robot mặc định khi tự tạo kho: kho S (S, S01, S02…) chỉ dùng robot đội gầm (latent) = 2; kho khác nâng hạ = 1
ROBOT_LATENT_PREFIXES = ("S",)


def default_robot(group: str) -> int:
    return 2 if group.startswith(ROBOT_LATENT_PREFIXES) else 1

log = logging.getLogger("locations")
CODE_RE = re.compile(r"[A-Z0-9]+(-[A-Z0-9]+)+")


def norm(code) -> str:
    """'a1.16.1' / ' A1-16-1 ' -> 'A1-16-1'. Dấu '.' (định dạng cũ) đổi sang '-'."""
    s = str(code or "").strip().upper().replace(".", "-")
    return re.sub(r"\s+", "", s)


def abs_path(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


def _cell(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _sheet_rows(p: str) -> list[list]:
    from openpyxl import load_workbook
    out = []
    for ro in (True, False):   # một số file RCS ghi sai kích thước sheet -> read_only chỉ thấy 1 ô
        wb = load_workbook(p, read_only=ro, data_only=True)
        out = [[list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets]
        wb.close()
        if any(len(s) > 1 for s in out):
            break
    return out


def read_highbay(path: str) -> list[dict]:
    """Đọc file vị trí (.xlsx / .csv): form 14 cột có tiêu đề, hoặc bố cục RAW không tiêu đề."""
    p = abs_path(path)
    ext = os.path.splitext(p)[1].lower()
    if ext == ".csv":
        with open(p, encoding="utf-8-sig", newline="") as f:
            sheets = [[list(r) for r in csv.reader(f)]]
    elif ext in (".xlsx", ".xlsm"):
        sheets = _sheet_rows(p)
    elif ext == ".json":
        return _read_json_legacy(p)
    else:
        raise ValueError(f"Không đọc được định dạng {ext}")
    for rows in sheets:
        if rows and any(str(c or "").strip() == "Location Code" for c in rows[0]):
            return _parse_14(rows)
    for rows in sheets:
        if rows and any(len(r) > RAW_COLS["code"] and CODE_RE.fullmatch(norm(r[RAW_COLS["code"]]))
                        for r in rows[:20]):
            return _parse_raw(rows)
    raise ValueError("Không nhận ra file: cần form highbay 14 cột (có cột 'Location Code') hoặc bố cục RAW "
                     "(mã điểm ở cột K)")


def _dedupe(rows):
    out, seen = [], set()
    for row in rows:
        if row["code"] and row["code"] not in seen:
            seen.add(row["code"])
            out.append(row)
    return out


def _parse_14(src: list[list]) -> list[dict]:
    head = {str(c or "").strip(): i for i, c in enumerate(src[0])}
    idx = {k: head.get(t) for k, t in HIGHBAY}
    out = []
    for r in src[1:]:
        row = {k: _cell(r[i]) if i is not None and i < len(r) else None for k, i in idx.items()}
        row["code"] = norm(row["code"])
        row["node"] = str(row["node"]).strip() if row["node"] not in (None, "") else ""
        out.append(row)
    return _dedupe(out)


def _parse_raw(src: list[list]) -> list[dict]:
    out = []
    for r in src:
        g = lambda k: _cell(r[RAW_COLS[k]]) if len(r) > RAW_COLS[k] else None  # noqa: E731
        code = norm(g("code"))
        if not CODE_RE.fullmatch(code or ""):
            continue
        out.append(make_row(code, g("node") or "", level_height=g("level_height"), coord=g("coord")))
    return _dedupe(out)


def _read_json_legacy(p: str) -> list[dict]:
    """File JSON cũ (v0.1-0.2: zones/ranges) -> dòng highbay (chỉ ô kệ)."""
    with open(p, encoding="utf-8-sig") as f:
        data = json.load(f)
    out = []
    for z in data.get("zones", []):
        if z.get("kind", "RACK") != "RACK":
            continue
        out += [make_row(c) for c in z.get("codes", [])]
        for rng in z.get("ranges", []):
            for col in range(rng["cols"][0], rng["cols"][1] + 1):
                for lv in range(rng["levels"][0], rng["levels"][1] + 1):
                    out.append(make_row(f"{rng['rack']}-{col}-{lv}"))
    return _dedupe(out)


def make_row(code: str, node: str = "", x=None, y=None, **kw) -> dict:
    """Dòng highbay mới; tầng lấy theo số cuối của mã (A1-16-3 -> tầng 3)."""
    code = norm(code)
    lv = code.rsplit("-", 1)[-1]
    h = LEVEL_HEIGHTS[int(lv) - 1] if lv.isdigit() and 1 <= int(lv) <= len(LEVEL_HEIGHTS) else 0
    row = dict(DEFAULT_ROW, code=code, node=str(node or ""), level_height=h,
               coord=f"({x},{y},{h})" if x is not None and y is not None else None)
    row.update({k: v for k, v in kw.items() if k in HB_KEYS and v is not None})
    return row


def write_highbay(path, rows: list[dict], status: list[dict] | None = None) -> None:
    """Ghi file highbay (.xlsx): Sheet0 đúng 14 cột RCS; tuỳ chọn sheet TrangThai."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet0"
    ws.append([t for _, t in HIGHBAY])
    for r in rows:
        ws.append([r.get(k) for k in HB_KEYS])
    if status is not None:
        st = wb.create_sheet("TrangThai")
        st.append(["Location Code", "Kho", "Loại", "Trạng thái", "Pallet", "Task", "Xuất lúc"])
        t = now().strftime("%Y-%m-%d %H:%M:%S")
        for s in status:
            st.append([s.get("code"), s.get("zone"), s.get("kind"), s.get("state"), s.get("pallet_id"), s.get("task"), t])
    if isinstance(path, io.BytesIO):
        wb.save(path)
        return
    p = abs_path(path)
    tmp = p + ".tmp.xlsx"
    wb.save(tmp)
    if os.path.exists(p):
        shutil.copyfile(p, p + ".bak")
    os.replace(tmp, p)


def write_raw(path, rows: list[dict], status: list[dict] | None = None) -> None:
    """Ghi bố cục RAW như "2500 RAW.xlsx" (không tiêu đề): D=Node, G=Rack, H=Cột, I=Tầng, K=Mã, M=Level Height,
    O=Toạ độ; tuỳ chọn sheet TrangThai."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for r in rows:
        parts = r["code"].split("-")
        line = [None] * 15
        node = str(r.get("node") or "")
        line[RAW_COLS["node"]] = int(node) if node.isdigit() else (node or None)
        line[RAW_COLS["rack"]] = parts[0]
        if len(parts) >= 3:
            line[RAW_COLS["col"]] = int(parts[1]) if parts[1].isdigit() else parts[1]
            line[RAW_COLS["level"]] = int(parts[-1]) if parts[-1].isdigit() else parts[-1]
        line[RAW_COLS["code"]] = r["code"]
        line[RAW_COLS["level_height"]] = r.get("level_height")
        line[RAW_COLS["coord"]] = r.get("coord")
        ws.append(line)
    if status is not None:
        st = wb.create_sheet("TrangThai")
        st.append(["Location Code", "Kho", "Loại", "Trạng thái", "Pallet", "Task", "Xuất lúc"])
        t = now().strftime("%Y-%m-%d %H:%M:%S")
        for s in status:
            st.append([s.get("code"), s.get("zone"), s.get("kind"), s.get("state"), s.get("pallet_id"), s.get("task"), t])
    wb.save(path)


def _sort_key(code: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", code)]


def prefix_group(code: str) -> str:
    """Nhóm tiền tố để tự tạo kho: chữ cái đầu của mã (C24-… -> C). S02 tách kho riêng theo KNOWN_WH."""
    rack = code.split("-")[0]
    for p in sorted(KNOWN_WH, key=len, reverse=True):
        if len(p) > 1 and rack.startswith(p):
            return p
    m = re.match(r"[A-Z]+", rack)
    return m.group() if m else rack


# ====================================================================== kho
class Warehouses:
    """Danh mục kho trong file JSON dùng chung: [{code, name, prefixes, wh_code, robot, store, active}]."""

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        self._list: list[dict] = []
        self._mtime = None
        self.reload()

    def reload(self):
        p = abs_path(self.path)
        with self.lock:
            if os.path.exists(p):
                with open(p, encoding="utf-8-sig") as f:
                    self._list = json.load(f).get("warehouses", [])
                self._mtime = os.path.getmtime(p)
            else:
                self._list = []

    def changed(self) -> bool:
        try:
            return os.path.getmtime(abs_path(self.path)) != self._mtime
        except OSError:
            return False

    def all(self) -> list[dict]:
        with self.lock:
            return [dict(w) for w in self._list]

    def save(self, items: list[dict]):
        clean_items, seen = [], set()
        for w in items:
            code = norm(w.get("code")).replace("-", "_")
            prefixes = [norm(p) for p in (w.get("prefixes") or []) if norm(p)]
            if not code or not prefixes:
                raise ValueError("Mỗi kho cần mã kho và ít nhất 1 tiền tố điểm")
            if code in seen:
                raise ValueError(f"Trùng mã kho {code}")
            seen.add(code)
            clean_items.append({"code": code, "name": str(w.get("name") or f"Kho {code}").strip(),
                                "prefixes": prefixes, "wh_code": str(w.get("wh_code") or "WH_TB").strip(),
                                "robot": int(w.get("robot") or 1), "store": bool(w.get("store", False)),
                                "active": bool(w.get("active", True))})
        all_p = [p for w in clean_items for p in w["prefixes"]]
        dup = {p for p in all_p if all_p.count(p) > 1}
        if dup:
            raise ValueError("Tiền tố dùng cho 2 kho: " + ", ".join(sorted(dup)))
        p = abs_path(self.path)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"warehouses": clean_items}, f, ensure_ascii=False, indent=2)
        if os.path.exists(p):
            shutil.copyfile(p, p + ".bak")
        os.replace(tmp, p)
        self.reload()

    def of(self, code: str) -> str:
        """Kho của một điểm: tiền tố khớp dài nhất."""
        best, blen = NO_WH, -1
        for w in self.all():
            for p in w["prefixes"]:
                if code.startswith(p) and len(p) > blen:
                    best, blen = w["code"], len(p)
        return best

    def ensure_for(self, codes) -> list[str]:
        """Tạo kho cho mọi nhóm tiền tố chưa có kho (khi nạp file). Trả các kho mới."""
        groups = sorted({prefix_group(c) for c in codes if self.of(c) == NO_WH})
        if not groups:
            return []
        items = self.all()
        has_store = any(w.get("store") for w in items)
        for g in groups:
            items.append({"code": g, "name": KNOWN_WH.get(g, f"Kho {g}"), "prefixes": [g], "wh_code": "WH_TB",
                          "robot": default_robot(g), "store": (g == "C" and not has_store), "active": True})
        self.save(items)
        return groups


# ====================================================================== vị trí
class LocationStore:
    """Danh sách ô của một app: file highbay (dùng chung) + trạng thái ô trong collection `locations`."""

    def __init__(self, db, cfg: dict, stations=lambda: [], on_removed=None):
        self.coll = db["locations"]
        self.coll.create_index("code", unique=True)
        self.coll.create_index([("zone", 1), ("state", 1)])
        self.cfg = cfg                       # cfg["locations"]: {file, warehouses}
        self.stations = stations             # -> [mã điểm sàn]
        self.on_removed = on_removed         # (codes) -> xoá đối tượng gán với các điểm bị bỏ
        self.lock = threading.RLock()
        self._mtime = None
        self.last_error = ""
        self.wh = Warehouses(self.cfg["locations"].get("warehouses") or "config/warehouses.json")
        self._pending: dict[str, dict] = {}  # token -> file vừa tải lên chờ áp dụng

    @property
    def file(self) -> str:
        return self.cfg["locations"].get("file") or "config/locations.xlsx"

    def rows(self) -> list[dict]:
        p = abs_path(self.file)
        return read_highbay(self.file) if os.path.exists(p) else []

    def load(self) -> dict:
        """Đọc file + điểm sàn -> collection. Điểm không còn trong file bị xoá (kèm đối tượng gán qua on_removed);
        riêng ô robot đang tới (BUSY) thì giữ lại, đánh dấu `removed`, xoá ở lần nạp sau."""
        with self.lock:
            try:
                rows = self.rows()
                self._mtime = self._file_mtime()
                self.wh.reload()
                self.last_error = ""
            except Exception as e:
                self.last_error = str(e)
                log.error("Đọc file vị trí %s lỗi: %s", self.file, e)
                return {"ok": False, "msg": str(e)}
            keep, added = set(), 0
            for r in rows:
                keep.add(r["code"])
                res = self.coll.update_one(
                    {"code": r["code"]},
                    {"$set": {"zone": self.wh.of(r["code"]), "kind": "RACK", "node": r.get("node", ""), "hb": r,
                              "removed": False},
                     "$setOnInsert": {"state": EMPTY, "pallet_id": None, "task": None}}, upsert=True)
                added += 1 if res.upserted_id else 0
            for code in {norm(c) for c in self.stations() if c}:
                keep.add(code)
                self.coll.update_one({"code": code},
                                     {"$set": {"zone": STATION_ZONE, "kind": "STATION", "removed": False},
                                      "$unset": {"hb": ""},
                                      "$setOnInsert": {"state": EMPTY, "pallet_id": None, "task": None}}, upsert=True)
            gone = [d["code"] for d in self.coll.find({"code": {"$nin": list(keep)}, "state": {"$ne": BUSY}}, {"code": 1})]
            if gone:
                if self.on_removed:
                    try:
                        self.on_removed(gone)
                    except Exception:
                        log.exception("xoá đối tượng gán với điểm bị bỏ lỗi")
                self.coll.delete_many({"code": {"$in": gone}})
            held = self.coll.update_many({"code": {"$nin": list(keep)}}, {"$set": {"removed": True}}).modified_count
            r = {"ok": True, "file": self.file, "total": len(rows), "added": added, "removed": len(gone),
                 "kept_busy": held}
            log.info("Nạp vị trí: %s", r)
            return r

    def _file_mtime(self):
        try:
            return os.path.getmtime(abs_path(self.file))
        except OSError:
            return None

    def watch(self, every: float = 5.0):
        """Tự nạp lại khi file vị trí / file kho đổi (app kia lưu, hoặc sửa tay bằng Excel)."""
        def run():
            while True:
                time.sleep(every)
                try:
                    m = self._file_mtime()
                    if (m and m != self._mtime) or self.wh.changed():
                        log.info("File vị trí / kho đổi -> nạp lại")
                        self.load()
                except Exception:
                    log.exception("watch vị trí lỗi")
        threading.Thread(target=run, name="loc-watch", daemon=True).start()

    # ---------------------------------------------------------------- giao diện
    def info(self) -> dict:
        p = abs_path(self.file)
        counts = {r["_id"]: r["n"] for r in self.coll.aggregate(
            [{"$match": {"kind": "RACK"}}, {"$group": {"_id": "$zone", "n": {"$sum": 1}}}])}
        whs = self.wh.all()
        for w in whs:
            w["count"] = counts.get(w["code"], 0)
        return {"file": self.file, "exists": os.path.exists(p), "error": self.last_error,
                "count": self.coll.count_documents({"kind": "RACK"}), "warehouses": whs,
                "unassigned": counts.get(NO_WH, 0),
                "stations": sorted(x["code"] for x in self.coll.find({"kind": "STATION"}, {"code": 1})),
                "columns": [{"key": k, "title": t} for k, t in HIGHBAY]}

    def search(self, q: str = "", page: int = 1, size: int = 100, wh: str = "") -> dict:
        f: dict = {"kind": "RACK"}
        if q:
            f["code"] = {"$regex": "^" + re.escape(norm(q))}
        if wh:
            f["zone"] = wh
        total = self.coll.count_documents(f)
        docs = list(self.coll.find(f, {"_id": 0}).sort("code", 1).skip((page - 1) * size).limit(size))
        return {"total": total, "page": page, "size": size, "rows": docs}

    def _save_rows(self, rows: list[dict]) -> dict:
        rows.sort(key=lambda r: _sort_key(r["code"]))
        write_highbay(self.file, rows)
        return self.load()

    def upsert(self, row: dict, old_code: str = "") -> dict:
        row = {k: row.get(k) for k in HB_KEYS}
        row["code"] = norm(row["code"])
        if not CODE_RE.fullmatch(row["code"] or ""):
            return {"ok": False, "msg": "Mã vị trí dạng Rack-Cột-Tầng, vd. S02-257-1"}
        for k in ("layer", "fork_height", "level_height"):
            if row.get(k) not in (None, ""):
                try:
                    row[k] = int(float(row[k]))
                except ValueError:
                    return {"ok": False, "msg": f"{k} phải là số"}
        with self.lock:
            rows = self.rows()
            old = norm(old_code) or row["code"]
            if old != row["code"] and any(r["code"] == row["code"] for r in rows):
                return {"ok": False, "msg": f"Đã có vị trí {row['code']}"}
            if old != row["code"] and self._in_use(old):
                return {"ok": False, "msg": f"Ô {old} đang có hàng / robot đang tới, không đổi mã được"}
            for i, r in enumerate(rows):
                if r["code"] == old:
                    rows[i] = row
                    break
            else:
                rows.append(row)
            self.wh.ensure_for([row["code"]])
            res = self._save_rows(rows)
        return {"ok": res["ok"], "msg": f"Đã lưu {row['code']} (kho {self.wh.of(row['code'])})"}

    def delete(self, codes: list[str]) -> dict:
        """Xoá điểm khỏi file; pallet đang gán ở điểm đó bị xoá theo (on_removed). Ô robot đang tới thì không xoá."""
        codes = {norm(c) for c in codes}
        busy = [d["code"] for d in self.coll.find({"code": {"$in": list(codes)}, "state": BUSY}, {"code": 1})]
        if busy:
            return {"ok": False, "msg": "Robot đang chở tới ô: " + ", ".join(sorted(busy))}
        with self.lock:
            rows = [r for r in self.rows() if r["code"] not in codes]
            self._save_rows(rows)
        return {"ok": True, "msg": f"Đã xoá {len(codes)} vị trí (kèm pallet gán ở đó)"}

    def bulk_add(self, rack: str, col_from: int, col_to: int, lv_from: int, lv_to: int, node_from: str = "",
                 node_step: int = 1, **kw) -> dict:
        """Thêm cả dải ô Rack-Cột-Tầng. Node: số node của cột đầu, cột sau +node_step (bỏ trống = không ghi)."""
        rack = norm(rack)
        if not rack or col_to < col_from or lv_to < lv_from or (col_to - col_from + 1) * (lv_to - lv_from + 1) > 5000:
            return {"ok": False, "msg": "Dải không hợp lệ (tối đa 5000 ô)"}
        with self.lock:
            rows = self.rows()
            have = {r["code"] for r in rows}
            new = []
            for i, col in enumerate(range(col_from, col_to + 1)):
                node = str(int(node_from) + i * node_step) if str(node_from).strip().isdigit() else ""
                for lv in range(lv_from, lv_to + 1):
                    code = f"{rack}-{col}-{lv}"
                    if code not in have:
                        new.append(make_row(code, node, layer=1, **kw))
            self.wh.ensure_for([r["code"] for r in new])
            self._save_rows(rows + new)
        return {"ok": True, "msg": f"Đã thêm {len(new)} vị trí ({rack}-{col_from}..{col_to}, tầng {lv_from}..{lv_to})"}

    def export_xlsx(self, fmt: str = "raw") -> bytes:
        """Xuất danh sách hiện tại: fmt="raw" (bố cục "2500 RAW") hoặc "14" (form 14 cột RCS), kèm sheet TrangThai."""
        buf = io.BytesIO()
        status = list(self.coll.find({}, {"_id": 0, "hb": 0}).sort([("kind", 1), ("code", 1)]))
        (write_highbay if fmt == "14" else write_raw)(buf, self.rows(), status)
        return buf.getvalue()

    # ---------------------------------------------------------------- nạp file mới: xem trước -> áp dụng
    def import_preview(self, name: str, raw: bytes) -> dict:
        ext = os.path.splitext(name or "")[1].lower()
        if ext not in (".xlsx", ".xlsm", ".csv"):
            return {"ok": False, "msg": "Chỉ nhận file .xlsx (form highbay 14 cột hoặc RAW) hoặc .csv"}
        tmp = abs_path(os.path.join("config", f"_upload_{secrets.token_hex(4)}{ext}"))
        with open(tmp, "wb") as f:
            f.write(raw)
        try:
            rows = read_highbay(tmp)
        finally:
            os.remove(tmp)
        if not rows:
            return {"ok": False, "msg": "File không có dòng vị trí nào"}
        token = secrets.token_hex(8)
        self._pending = {token: {"rows": rows, "name": name, "t": time.time()}}
        groups: dict[str, int] = {}
        for r in rows:
            g = self.wh.of(r["code"])
            g = prefix_group(r["code"]) if g == NO_WH else g
            groups[g] = groups.get(g, 0) + 1
        return {"ok": True, "token": token, "name": name, "total": len(rows),
                "groups": [{"wh": g, "count": n, "exists": any(w["code"] == g for w in self.wh.all())}
                           for g, n in sorted(groups.items())]}

    def _select(self, rows: list[dict], sel: dict) -> list[dict]:
        """sel = {kho: số điểm tối đa (0 = tất cả)}; chọn theo thứ tự mã."""
        out = []
        by: dict[str, list] = {}
        for r in sorted(rows, key=lambda r: _sort_key(r["code"])):
            g = self.wh.of(r["code"])
            g = prefix_group(r["code"]) if g == NO_WH else g
            by.setdefault(g, []).append(r)
        for g, lim in sel.items():
            lst = by.get(g, [])
            out += lst[:int(lim)] if int(lim or 0) > 0 else lst
        return out

    def import_diff(self, token: str, sel: dict) -> dict:
        p = self._pending.get(token)
        if not p:
            return {"ok": False, "msg": "Phiên nạp đã hết hạn, tải file lên lại"}
        new = self._select(p["rows"], sel)
        new_codes = {r["code"] for r in new}
        old_codes = {d["code"] for d in self.coll.find({"kind": "RACK"}, {"code": 1})}
        removed = sorted(old_codes - new_codes, key=_sort_key)
        in_use = [d for d in self.coll.find({"code": {"$in": removed}, "state": {"$in": [FULL, BUSY]}},
                                            {"_id": 0, "code": 1, "state": 1, "pallet_id": 1})]
        return {"ok": True, "selected": len(new), "added": sorted(new_codes - old_codes, key=_sort_key),
                "removed": removed, "kept": len(new_codes & old_codes), "in_use": in_use}

    def import_apply(self, token: str, sel: dict) -> dict:
        p = self._pending.get(token)
        if not p:
            return {"ok": False, "msg": "Phiên nạp đã hết hạn, tải file lên lại"}
        diff = self.import_diff(token, sel)
        with self.lock:
            created = self.wh.ensure_for([r["code"] for r in p["rows"]])   # đủ kho cho mọi tiền tố trong file
            rows = self._select(p["rows"], sel)
            res = self._save_rows(rows)
        self._pending.pop(token, None)
        return {"ok": res["ok"], "msg": f"Đã nạp {len(rows)} điểm: thêm {len(diff['added'])}, xoá {len(diff['removed'])}"
                                       + (f", tạo kho {', '.join(created)}" if created else ""),
                "created_warehouses": created, "result": res, "file_changed": True}

    def _in_use(self, code: str) -> bool:
        d = self.coll.find_one({"code": norm(code)})
        return bool(d and (d["state"] in (FULL, BUSY) or d.get("task")))


# ====================================================================== API dùng chung
def add_routes(app, store: LocationStore, save_cfg, manage_warehouses: bool = True) -> None:
    """API vị trí / kho dùng chung cho WMS ảo và WCS B."""

    @app.get("/api/locations/info")
    def loc_info():
        return store.info()

    @app.get("/api/locations/search")
    def loc_search(q: str = "", page: int = 1, size: int = 100, wh: str = ""):
        return store.search(q, max(page, 1), min(max(size, 10), 500), wh)

    @app.post("/api/locations/row")
    async def loc_row(req: Request):
        b = await req.json()
        return store.upsert(b.get("row") or {}, b.get("old_code", ""))

    @app.post("/api/locations/delete")
    async def loc_delete(req: Request):
        b = await req.json()
        return store.delete(b.get("codes") or [])

    @app.post("/api/locations/bulk")
    async def loc_bulk(req: Request):
        b = await req.json()
        try:
            return store.bulk_add(b.get("rack", ""), int(b["col_from"]), int(b["col_to"]), int(b["lv_from"]),
                                  int(b["lv_to"]), str(b.get("node_from", "")), int(b.get("node_step") or 1))
        except (KeyError, ValueError) as e:
            return {"ok": False, "msg": f"Thiếu / sai số: {e}"}

    @app.get("/api/locations/export")
    def loc_export(fmt: str = "raw"):
        fmt = "14" if fmt == "14" else "raw"
        name = f"locations_{'RAW' if fmt == 'raw' else 'highbay14'}_{now().strftime('%Y%m%d_%H%M')}.xlsx"
        return Response(store.export_xlsx(fmt), headers={"Content-Disposition": f'attachment; filename="{name}"'},
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.post("/api/locations/import/preview")
    async def loc_import_preview(req: Request):
        b = await req.json()
        try:
            return store.import_preview(b.get("name", ""), base64.b64decode((b.get("b64") or "").split(",")[-1]))
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    @app.post("/api/locations/import/diff")
    async def loc_import_diff(req: Request):
        b = await req.json()
        return store.import_diff(b.get("token", ""), b.get("select") or {})

    @app.post("/api/locations/import/apply")
    async def loc_import_apply(req: Request):
        b = await req.json()
        try:
            r = store.import_apply(b.get("token", ""), b.get("select") or {})
        except Exception as e:
            log.exception("nạp file vị trí lỗi")
            return {"ok": False, "msg": str(e)}
        if r.pop("file_changed", False):
            save_cfg()
        return r

    @app.post("/api/locations/reload")
    def loc_reload():
        return store.load()

    @app.get("/api/warehouses")
    def wh_list():
        return store.info()["warehouses"]

    @app.put("/api/warehouses")
    async def wh_save(req: Request):
        if not manage_warehouses:
            return {"ok": False, "msg": "Danh mục kho sửa ở WMS ảo"}
        b = await req.json()
        try:
            store.wh.save(b.get("warehouses") or [])
        except ValueError as e:
            return {"ok": False, "msg": str(e)}
        r = store.load()   # gán lại kho cho từng điểm
        return {"ok": True, "msg": f"Đã lưu {len(store.wh.all())} kho, gán lại {r.get('total', 0)} điểm"}
