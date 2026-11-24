"""Danh sách vị trí: nạp từ JSON / CSV / Excel, mã chuẩn hoá dạng `A1-16-1` (dấu `-`, chữ hoa).

Tên điểm trên RCS trùng mã vị trí nên WCS gửi thẳng mã vào taskPath, danh sách này chỉ để
kiểm tra mã hợp lệ, gán zone và khoá / giữ chỗ ô.
"""
from __future__ import annotations

import csv
import json
import os
import re

from .common import ROOT

EMPTY, FULL, BUSY, LOCKED = "EMPTY", "FULL", "BUSY", "LOCKED"


def norm(code) -> str:
    """'a1.16.1' / ' A1-16-1 ' -> 'A1-16-1'. Dấu '.' (định dạng cũ) đổi sang '-'."""
    s = str(code or "").strip().upper().replace(".", "-")
    return re.sub(r"\s+", "", s)


def load_file(path: str) -> list[dict]:
    """Trả [{code, zone, kind}] . kind: RACK (ô kệ) | STATION (bàn định danh, đầu băng tải, bãi...)."""
    if not os.path.isabs(path):
        path = os.path.join(ROOT, path)
    ext = os.path.splitext(path)[1].lower()
    rows: list[dict] = []
    if ext == ".json":
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        for zone_block in data.get("zones", []):
            zone, kind = zone_block.get("zone", ""), zone_block.get("kind", "RACK")
            for c in zone_block.get("codes", []):
                rows.append({"code": norm(c), "zone": zone, "kind": kind})
            for rng in zone_block.get("ranges", []):
                # {"rack":"S02","cols":[250,260],"levels":[1,4]} -> S02-250-1 ...
                for col in range(rng["cols"][0], rng["cols"][1] + 1):
                    for lv in range(rng["levels"][0], rng["levels"][1] + 1):
                        rows.append({"code": norm(f"{rng['rack']}-{col}-{lv}"), "zone": zone, "kind": kind})
    elif ext == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                code = r.get("code") or r.get("Location Code") or r.get("location")
                if code:
                    rows.append({"code": norm(code), "zone": r.get("zone", ""), "kind": r.get("kind") or "RACK"})
    elif ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        for ws in wb.worksheets:
            it = ws.iter_rows(values_only=True)
            head = None
            for row in it:
                cells = [str(c).strip() if c is not None else "" for c in row]
                if head is None:
                    if "Location Code" in cells:
                        head = {name: i for i, name in enumerate(cells)}
                    continue
                code = cells[head["Location Code"]]
                if code:
                    zone = cells[head["Zone"]] if "Zone" in head else ""
                    rows.append({"code": norm(code), "zone": zone, "kind": "RACK"})
    else:
        raise ValueError(f"Không đọc được định dạng {ext}")
    seen, out = set(), []
    for r in rows:
        if r["code"] and r["code"] not in seen:
            seen.add(r["code"])
            out.append(r)
    return out


def sync_collection(coll, rows: list[dict]) -> dict:
    """Thêm vị trí mới, cập nhật zone/kind; không đụng trạng thái ô đang có."""
    coll.create_index("code", unique=True)
    added = 0
    for r in rows:
        res = coll.update_one({"code": r["code"]},
                              {"$set": {"zone": r["zone"], "kind": r["kind"]},
                               "$setOnInsert": {"state": EMPTY, "pallet_id": None, "task": None}},
                              upsert=True)
        added += 1 if res.upserted_id else 0
    return {"total": len(rows), "added": added}


def abs_path(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


def read_def(path: str) -> dict:
    """Nội dung file vị trí dạng JSON để sửa trên giao diện (file Excel/CSV thì chỉ trả danh sách)."""
    p = abs_path(path)
    if path.lower().endswith(".json") and os.path.exists(p):
        with open(p, encoding="utf-8-sig") as f:
            return {"file": path, "editable": True, "def": json.load(f)}
    return {"file": path, "editable": False, "def": None}


def write_def(path: str, data: dict) -> int:
    """Ghi file vị trí JSON (đọc thử bản tạm trước khi thay file thật). Trả số vị trí."""
    if not isinstance(data, dict) or not isinstance(data.get("zones"), list):
        raise ValueError("Cần {zones: [...]}")
    p = abs_path(path)
    tmp = p + ".tmp.json"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        n = len(load_file(tmp))
    except Exception:
        os.remove(tmp)
        raise
    os.replace(tmp, p)
    return n


def save_upload(name: str, raw: bytes) -> str:
    """Lưu file Excel / CSV / JSON tải lên vào config/, trả đường dẫn tương đối; kiểm tra đọc được."""
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.basename(name or "locations.xlsx"))
    ext = os.path.splitext(base)[1].lower()
    if ext not in (".xlsx", ".xlsm", ".csv", ".json"):
        raise ValueError("Chỉ nhận .xlsx / .csv / .json")
    rel = os.path.join("config", "upload_" + base)
    p = abs_path(rel)
    with open(p, "wb") as f:
        f.write(raw)
    rows = load_file(rel)
    if not rows:
        os.remove(p)
        raise ValueError("File không có mã vị trí nào (Excel cần cột 'Location Code')")
    return rel.replace("\\", "/")
