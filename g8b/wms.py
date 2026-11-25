"""WMS ảo - nói đúng API TKELog WMS V5 (bản WCS gốc đang dùng).

Mục tiêu cốt lõi: tạo lệnh định danh (sinh mã thùng cho bàn định danh in / quét), trả lời yêu cầu định danh,
gọi robot cất pallet đầy, lưu pallet / thùng đã định danh để xuất hàng (đầu xuất).

Danh mục: kho (ghép điểm theo tiền tố), sản phẩm (quy cách thùng, quy cách pallet, chuyền), vị trí (form highbay).
Lệnh sản xuất: nhập / xuất file Excel định dạng G8, sửa tay; người vận hành bấm "Định danh" để chuyển lệnh sang
định danh (sinh lệnh định danh PO), lệnh hiện tiến độ theo pallet / thùng, tự "Hoàn thành" khi đủ pallet.
Lệnh WMS: định danh (PO / ASN), luân chuyển pallet (Move), xuất hàng (DO), cập nhật vị trí (sửa tay, không robot).

WCS -> WMS: Login, Pallet_Location, Finished_Goods, OB_Pallet, Location_Stocks (định dạng {Auth, Data}).
WMS -> WCS: move_pallet, identify-cartons, identify-import, identify-cartons/cancel, export_items (qua outbox).
Chạy: python -m g8b.wms
"""
from __future__ import annotations

import base64
import copy
import logging
import math
import os
import re
import secrets
import threading
import time
from datetime import timedelta
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from . import locations as L
from . import prodfiles as F
from .common import (MsgLog, ROOT, ci_get, clean, deep_merge, hourly, site_port, load_config, mongo_db, now, save_config,
                     setup_logging, today_start)
from .outbox import Outbox

VERSION = "0.7.0"

DEFAULTS: dict = {
    "server": {"host": "0.0.0.0", "port": 9100},
    "mongo": {"uri": "mongodb://127.0.0.1:27018", "db": "g8b_wms"},
    "wcs": {"url": "http://127.0.0.1:9000"},
    "users": {"WCS_API_User": "123456"},
    "token_minutes": 480,
    "locations": {"file": "config/locations.xlsx", "warehouses": "config/warehouses.json"},
    # vị trí sàn của từng line định danh / băng tải xuất (điểm robot lấy / trả pallet)
    "stations": {},
    "auto_putaway": True,       # pallet định danh xong -> tự gọi robot cất vào ô đã gán
    "codes": {"po_prefix": "VAS", "pallet_prefix": "LPNB", "carton_prefix": "G8B"},
    "outbox": {"retry_s": 5, "timeout_s": 10},
    "messages_keep_days": 14,
    "fail_next": {},            # dùng khi test: {"Finished_Goods": 1} -> từ chối 1 lần
}

# Sản phẩm mẫu (lấy từ bản tin thật), chỉ nạp khi danh mục trống
SAMPLE_PRODUCTS = [
    {"item_code": "TRU_G01_50W_AT", "item_name": "Bóng Led trụ nhôm T50, model T50-125/G01", "group": "LED trụ",
     "inner_carton": 6, "inner_pallet": 32, "gw": 1.637, "nw": 0},
    {"item_code": "TRU_G01_30W_AV", "item_name": "Bóng Led trụ nhôm T30, model T30-100/G01 ánh sáng vàng",
     "group": "LED trụ", "inner_carton": 12, "inner_pallet": 30, "gw": 2.1, "nw": 0},
    {"item_code": "TRU_V01_10W_AT", "item_name": "Bóng Led trụ V01 10W ánh sáng trắng", "group": "LED trụ",
     "inner_carton": 48, "inner_pallet": 40, "gw": 4.2, "nw": 0},
]
TYPE_MOVE = {1: "Cất pallet băng tải vào bãi", 2: "Gọi pallet rỗng", 3: "Cất pallet định danh vào kho",
             4: "Xuất pallet ra bãi", 5: "Châm hàng xuất", 6: "Xuất pallet lên băng chuyền", 7: "Xuất NVL",
             10: "Move pallet to băng chuyền", 11: "Chuyển vị trí pallet", 12: "Cất pallet vào kệ",
             13: "Cất pallet NVL dư", 15: "Chuyển vị trí nội bộ"}
ROBOTS = {1: "Robot nâng hạ (xe nâng)", 2: "Robot đội gầm"}
PALLET_LIVE = ("PLANNED", "IDENTIFIED", "STORED")
# lệnh sản xuất: Chờ định danh -> (người vận hành bấm Định danh) Đang định danh -> Hoàn thành; Đã hủy
PROD_STATUS = {"WAITING": "Chờ định danh", "IDENTIFYING": "Đang định danh", "DONE": "Hoàn thành", "CANCELLED": "Đã hủy"}
PROD_FIELDS = ("code", "line", "wh_in", "wh_out", "prod_date", "type", "item_code", "note") + F.NUM_KEYS

log = logging.getLogger("wms")


def tke(ok: bool = True, desc: str = "Success", data: Any = None, code: int | None = None) -> dict:
    return {"message": {"message_Code": code or (200 if ok else 201), "message_Desc": desc, "fragment_Code": ""},
            "data": data if data is not None else {}}


class WMS:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.db = mongo_db(cfg["mongo"]["uri"], cfg["mongo"]["db"])
        self.msg = MsgLog(self.db, cfg["messages_keep_days"])
        o = cfg["outbox"]
        self.outbox = Outbox(self.db, self.msg, o["retry_s"], 0, o["timeout_s"])
        self.outbox.register("wcs", self._wcs_accept, on_dead=self._wcs_dead)
        self.orders = self.db["orders"]
        self.pallets = self.db["pallets"]
        self.products = self.db["products"]
        self.locs = self.db["locations"]
        self.tokens = self.db["tokens"]
        self.counters = self.db["counters"]
        self.events = self.db["events"]
        self.prod = self.db["prod_orders"]
        self._pending: dict[str, dict] = {}   # file đã đọc, chờ áp dụng (token -> dữ liệu)
        self._lock = threading.Lock()
        self.fail_next = dict(cfg.get("fail_next", {}))
        self.tokens.create_index("token", unique=True)
        self.tokens.create_index("expire", expireAfterSeconds=0)
        self.orders.create_index("order_id", unique=True)
        self.orders.create_index("created_at")
        self.pallets.create_index("pallet_id", unique=True)
        self.pallets.create_index("location")
        self.pallets.create_index("cartons")
        self.products.create_index("item_code", unique=True)
        self.prod.create_index("key", unique=True)
        self.prod.create_index([("status", ASCENDING), ("created_at", DESCENDING)])
        self.events.create_index("t", expireAfterSeconds=30 * 86400)
        if self.products.count_documents({}) == 0:
            for p in SAMPLE_PRODUCTS:
                self.products.insert_one(dict(p, active=True, created_at=now()))
        self.locstore = L.LocationStore(self.db, self.cfg, lambda: list(self.cfg["stations"].values()),
                                        on_removed=self._locations_removed)
        self.locstore.load()

    # ------------------------------------------------------------------ tiện ích
    def seq(self, name: str) -> int:
        d = self.counters.find_one_and_update({"_id": name}, {"$inc": {"v": 1}}, upsert=True,
                                              return_document=ReturnDocument.AFTER)
        return int(d["v"])

    def event(self, text: str, **ref):
        log.info("%s %s", text, ref or "")
        self.events.insert_one({"t": now(), "text": text, "ref": ref})

    def _wcs_accept(self, status, resp):
        code = resp.get("code") if isinstance(resp, dict) else None
        if status == 200 and code == 200:
            return True, ""
        if status == 200 and code == 201:
            return False, f"WCS từ chối: {resp.get('desc')}", True
        return False, f"HTTP {status} {resp}"

    def _wcs_dead(self, job, resp, why):
        oid = job["ref"].get("order")
        self.orders.update_one({"order_id": oid}, {"$set": {"status": "REJECTED", "error": why}})
        self.event(f"WCS từ chối lệnh {oid}: {why}")

    def send(self, path: str, body: dict, order_id: str, key: str | None = None):
        self.outbox.put("wcs", key or f"wcs:{order_id}", self.cfg["wcs"]["url"].rstrip("/") + path, body,
                        {"order": order_id}, "WCS")

    def wh(self, code: str) -> dict:
        return next((w for w in self.locstore.wh.all() if w["code"] == code), {})

    def wh_code_of(self, location: str) -> str:
        return self.wh(self.locstore.wh.of(L.norm(location))).get("wh_code") or "WH_TB"

    def store_wh(self) -> str:
        whs = [w for w in self.locstore.wh.all() if w.get("active", True)]
        st = [w for w in whs if w.get("store")]
        return (st or whs or [{"code": ""}])[0]["code"]

    def reserve_location(self, pallet_id: str, wh: str) -> str:
        """Chọn ô trống trong kho và giữ chỗ cho pallet."""
        d = self.locs.find_one_and_update(
            {"zone": wh, "kind": "RACK", "state": L.EMPTY, "task": None},
            {"$set": {"task": f"reserve:{pallet_id}"}}, sort=[("code", ASCENDING)])
        return d["code"] if d else ""

    def _release_reserve(self, pallet_id: str):
        self.locs.update_many({"task": f"reserve:{pallet_id}"}, {"$set": {"task": None}})

    def _place(self, pallet_id: str, code: str):
        """Đặt pallet vào ô (cập nhật cả ô cũ)."""
        code = L.norm(code)
        p = self.pallets.find_one({"pallet_id": pallet_id}) or {}
        old = p.get("location") or ""
        if old and old != code:
            self.locs.update_one({"code": old, "pallet_id": pallet_id},
                                 {"$set": {"state": L.EMPTY, "pallet_id": None, "task": None}})
        if code:
            self.locs.update_one({"code": code}, {"$set": {"state": L.FULL, "pallet_id": pallet_id, "task": None}})
        self._release_reserve(pallet_id)
        self.pallets.update_one({"pallet_id": pallet_id}, {"$set": {"location": code, "wh": self.locstore.wh.of(code)
                                                                    if code else "", "updated_at": now()}})

    def _locations_removed(self, codes: list[str]):
        """Điểm bị bỏ khỏi file vị trí: xoá pallet đang nằm ở đó, bỏ giữ chỗ của pallet chưa cất."""
        gone = list(self.pallets.find({"location": {"$in": codes}}, {"pallet_id": 1}))
        if gone:
            self.pallets.delete_many({"location": {"$in": codes}})
        self.pallets.update_many({"assigned_location": {"$in": codes}}, {"$set": {"assigned_location": ""}})
        self.event(f"Bỏ {len(codes)} điểm khỏi danh sách vị trí, xoá {len(gone)} pallet gán ở đó",
                   pallets=[g["pallet_id"] for g in gone][:50])

    # ------------------------------------------------------------------ sản phẩm
    def product_save(self, b: dict) -> dict:
        code = str(b.get("item_code") or "").strip()
        if not code or not str(b.get("item_name") or "").strip():
            return {"ok": False, "msg": "Cần mã và tên sản phẩm"}
        try:
            doc = {"item_code": code, "item_name": str(b["item_name"]).strip(), "group": str(b.get("group") or ""),
                   "inner_carton": int(b.get("inner_carton") or 0), "inner_pallet": int(b.get("inner_pallet") or 0),
                   "gw": float(b.get("gw") or 0), "nw": float(b.get("nw") or 0), "active": bool(b.get("active", True)),
                   "note": str(b.get("note") or ""), "lines": self._lines(b.get("lines")), "updated_at": now()}
        except (TypeError, ValueError):
            return {"ok": False, "msg": "Quy cách / khối lượng phải là số"}
        if doc["inner_carton"] <= 0 or doc["inner_pallet"] <= 0:
            return {"ok": False, "msg": "Quy cách thùng (chiếc/thùng) và quy cách pallet (thùng/pallet) phải > 0"}
        old = str(b.get("old_code") or code)
        if old != code and self.products.find_one({"item_code": code}):
            return {"ok": False, "msg": f"Đã có sản phẩm {code}"}
        self.products.update_one({"item_code": old}, {"$set": doc, "$setOnInsert": {"created_at": now()}}, upsert=True)
        return {"ok": True, "msg": f"Đã lưu sản phẩm {code}"}

    @staticmethod
    def _lines(v) -> list[str]:
        items = v if isinstance(v, list) else re.split(r"[,;\n]", str(v or ""))
        return [x.strip() for x in items if str(x).strip()]

    def _stash(self, data: dict) -> str:
        """Giữ dữ liệu file vừa đọc để bước "áp dụng" dùng lại (hết hạn sau 30 phút)."""
        t = time.time()
        self._pending = {k: v for k, v in self._pending.items() if t - v["t"] < 1800}
        token = secrets.token_hex(8)
        self._pending[token] = dict(data, t=t)
        return token

    def products_import_preview(self, name: str, raw: bytes) -> dict:
        try:
            rows = F.read_products(raw)
        except Exception as e:  # noqa: BLE001 - file lạ: báo lỗi cho người dùng
            return {"ok": False, "msg": f"Không đọc được file sản phẩm: {e}"}
        if not rows:
            return {"ok": False, "msg": "File không có sản phẩm nào"}
        have = {p["item_code"] for p in self.products.find({}, {"item_code": 1})}
        new = [r["item_code"] for r in rows if r["item_code"] not in have]
        spec = sum(1 for r in rows if r.get("inner_carton") or F.carton_spec_from_name(r["item_name"]))
        return {"ok": True, "token": self._stash({"kind": "products", "rows": rows, "name": name}), "name": name,
                "total": len(rows), "new": len(new), "update": len(rows) - len(new), "with_spec": spec,
                "lines": sum(len(r["lines"]) for r in rows), "new_codes": new[:30]}

    def products_import_apply(self, token: str) -> dict:
        pend = self._pending.pop(token, None)
        if not pend or pend.get("kind") != "products":
            return {"ok": False, "msg": "Phiên nạp file đã hết hạn, đọc lại file"}
        n_new = n_upd = 0
        for r in pend["rows"]:
            old = self.products.find_one({"item_code": r["item_code"]})
            doc = {"item_name": r["item_name"] or (old or {}).get("item_name") or r["item_code"], "lines": r["lines"],
                   "updated_at": now()}
            for k in ("inner_carton", "inner_pallet", "gw", "nw", "group", "active"):
                if k in r and r[k] not in ("", None):
                    doc[k] = int(r[k]) if k in ("inner_carton", "inner_pallet") else r[k]
            if not (old or {}).get("inner_carton") and not doc.get("inner_carton"):
                doc["inner_carton"] = F.carton_spec_from_name(r["item_name"])
            if old:
                ins = {"item_code": r["item_code"]}
                n_upd += 1
            else:
                ins = {"item_code": r["item_code"], "created_at": now()}
                for k, v in (("inner_carton", 0), ("inner_pallet", 0), ("gw", 0), ("nw", 0), ("group", ""),
                             ("active", True), ("note", "")):
                    if k not in doc:
                        ins[k] = v
                n_new += 1
            self.products.update_one({"item_code": r["item_code"]}, {"$set": doc, "$setOnInsert": ins}, upsert=True)
        miss = self.products.count_documents({"$or": [{"inner_carton": {"$lte": 0}}, {"inner_pallet": {"$lte": 0}}]})
        self.event(f"Nạp file sản phẩm {pend['name']}: {n_new} mới, {n_upd} cập nhật")
        return {"ok": True, "msg": f"Đã nạp {n_new} sản phẩm mới, cập nhật {n_upd}. {miss} sản phẩm chưa đủ quy cách "
                                   f"thùng / pallet (cần nhập trước khi định danh)."}

    def products_export(self) -> bytes:
        return F.write_products(list(self.products.find().sort("item_code", ASCENDING)))

    # ------------------------------------------------------------------ lệnh sản xuất
    @staticmethod
    def prod_key(code: str, line: str) -> str:
        return f"{str(code).strip()}|{F.line_id(line)}"

    def _prod_progress(self, o: dict) -> dict:
        """Tiến độ định danh của lệnh sản xuất: thùng / pallet đã định danh, pallet đã cất kệ."""
        ident = o.get("identify") or {}
        if not ident.get("order_id"):
            return {}
        st = {"PLANNED": 0, "IDENTIFIED": 0, "STORED": 0, "SHIPPED": 0, "CANCELLED": 0}
        ctn_done = 0
        for p in self.pallets.find({"order_id": ident["order_id"]}, {"status": 1, "cartons": 1, "ctn_done": 1}):
            st[p["status"]] = st.get(p["status"], 0) + 1
            if p["status"] in ("IDENTIFIED", "STORED", "SHIPPED"):
                ctn_done += int(p.get("ctn_done") or len(p.get("cartons") or []))
        total = sum(v for k, v in st.items() if k != "CANCELLED")
        done = st["IDENTIFIED"] + st["STORED"] + st["SHIPPED"]
        plan = int(ident.get("ctn") or 0)
        return {"ctn_plan": plan, "ctn_done": ctn_done, "pallets": total, "pallets_done": done,
                "pallets_stored": st["STORED"] + st["SHIPPED"], "pallets_cancelled": st["CANCELLED"],
                "pct": round(100 * ctn_done / plan) if plan else 0}

    def prod_list(self, status: str = "", q: str = "") -> list[dict]:
        f: dict = {}
        if status:
            f["status"] = {"$in": status.split(",")}
        if q:
            rx = {"$regex": re.escape(q), "$options": "i"}
            f["$or"] = [{"code": rx}, {"item_code": rx}, {"line": rx}, {"note": rx}]
        out = []
        prods = {p["item_code"]: p for p in self.products.find({}, {"item_code": 1, "item_name": 1, "inner_carton": 1,
                                                                   "inner_pallet": 1})}
        for o in self.prod.find(f).sort([("prod_date_sort", DESCENDING), ("code", DESCENDING)]).limit(2000):
            o = clean(o)
            prod = prods.get(o.get("item_code"))
            o["item_name"] = (prod or {}).get("item_name", "")
            o["product_ok"] = bool(prod and prod.get("inner_carton", 0) > 0 and prod.get("inner_pallet", 0) > 0)
            o["product_missing"] = prod is None
            o["progress"] = self._prod_progress(o)
            out.append(o)
        # lệnh đang định danh lên đầu, rồi lệnh chờ; trong nhóm: ngày SX mới trước
        rank = {"IDENTIFYING": 0, "WAITING": 1, "DONE": 2, "CANCELLED": 3}
        out.sort(key=lambda o: rank.get(o.get("status"), 9))
        return out

    @staticmethod
    def _date_sort(d: str) -> str:
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", d or "")
        return f"{m.group(3)}{int(m.group(2)):02d}{int(m.group(1)):02d}" if m else ""

    def _prod_doc(self, b: dict) -> dict:
        doc = {k: str(b.get(k) or "").strip() for k in PROD_FIELDS if k not in F.NUM_KEYS}
        for k in F.NUM_KEYS:
            doc[k] = F._num(b.get(k))
        doc["prod_date"] = F.fmt_date(b.get("prod_date"))
        doc["line_id"] = F.line_id(doc["line"])
        doc["prod_date_sort"] = self._date_sort(doc["prod_date"])
        return doc

    def prod_save(self, b: dict) -> dict:
        """Thêm / sửa tay lệnh sản xuất. Lệnh đang định danh chỉ sửa được ghi chú."""
        doc = self._prod_doc(b)
        if not doc["code"] or not doc["line"] or not doc["item_code"]:
            return {"ok": False, "msg": "Cần mã lệnh, Line và mã sản phẩm"}
        if doc["ctn"] <= 0:
            return {"ok": False, "msg": "Số kiện (số thùng thành phẩm) phải > 0"}
        key = self.prod_key(doc["code"], doc["line"])
        old_key = str(b.get("old_key") or key)
        old = self.prod.find_one({"key": old_key})
        if old_key != key and self.prod.find_one({"key": key}):
            return {"ok": False, "msg": f"Đã có lệnh {doc['code']} trên line {doc['line_id']}"}
        if old and old["status"] == "IDENTIFYING":
            self.prod.update_one({"_id": old["_id"]}, {"$set": {"note": doc["note"], "updated_at": now()}})
            return {"ok": True, "msg": "Lệnh đang định danh: chỉ lưu ghi chú"}
        status = str(b.get("status") or (old or {}).get("status") or "WAITING")
        if status not in ("WAITING", "DONE", "CANCELLED"):
            return {"ok": False, "msg": "Trạng thái sửa tay chỉ được: Chờ định danh / Hoàn thành / Đã hủy"}
        doc.update(key=key, status=status, updated_at=now())
        if old:
            self.prod.update_one({"_id": old["_id"]}, {"$set": doc})
        else:
            self.prod.insert_one(dict(doc, source="manual", status_text="", created_at=now()))
        self.event(f"{'Sửa' if old else 'Thêm'} tay lệnh sản xuất {doc['code']} line {doc['line_id']}")
        return {"ok": True, "msg": f"Đã lưu lệnh {doc['code']}", "key": key}

    def prod_delete(self, key: str) -> dict:
        o = self.prod.find_one({"key": key})
        if not o:
            return {"ok": False, "msg": "Không có lệnh"}
        if o["status"] == "IDENTIFYING":
            return {"ok": False, "msg": "Lệnh đang định danh - hủy định danh trước khi xoá"}
        self.prod.delete_one({"_id": o["_id"]})
        self.event(f"Xoá lệnh sản xuất {o['code']} line {o.get('line_id')}")
        return {"ok": True, "msg": f"Đã xoá lệnh {o['code']}"}

    def prod_start(self, key: str, line_id: str = "", ctn: int = 0, mfg_date: str = "", dest_wh: str = "") -> dict:
        """Người vận hành chuyển lệnh sang định danh: sinh lệnh định danh PO (PO_Name = mã lệnh sản xuất)."""
        o = self.prod.find_one({"key": key})
        if not o:
            return {"ok": False, "msg": "Không có lệnh"}
        if o["status"] != "WAITING":
            return {"ok": False, "msg": f"Lệnh đang ở trạng thái {PROD_STATUS.get(o['status'], o['status'])}, "
                                        "chỉ lệnh Chờ định danh mới chuyển sang định danh được"}
        prod = self.products.find_one({"item_code": o["item_code"]})
        if not prod:
            return {"ok": False, "msg": f"Sản phẩm {o['item_code']} chưa có trong danh mục"}
        line_id = line_id or o.get("line_id", "")
        ctn = int(ctn or o.get("ctn") or 0)
        r = self.create_identify("PO", line_id, o["item_code"], ctn, mfg_date or o.get("prod_date", ""), dest_wh,
                                 o["code"], prod_key=key)
        if not r["ok"]:
            return r
        self.prod.update_one({"_id": o["_id"]}, {"$set": {
            "status": "IDENTIFYING", "updated_at": now(),
            "identify": {"order_id": r["order_id"], "po_id": r["po_id"], "line_id": line_id, "ctn": ctn,
                         "pallets": r["pallets"], "started_at": now()}}})
        self.event(f"Lệnh sản xuất {o['code']} chuyển sang định danh tại {line_id}: {ctn} thùng = {r['pallets']} pallet")
        return dict(r, msg=f"Lệnh {o['code']} đang định danh tại {line_id}: {r['pallets']} pallet")

    def prod_cancel(self, key: str) -> dict:
        o = self.prod.find_one({"key": key})
        if not o or o["status"] != "IDENTIFYING":
            return {"ok": False, "msg": "Lệnh không ở trạng thái đang định danh"}
        r = self.cancel_identify(int(o["identify"]["po_id"]))
        self.prod.update_one({"_id": o["_id"]}, {"$set": {"status": "CANCELLED", "updated_at": now()}})
        return dict(r, msg=f"Đã hủy định danh lệnh {o['code']}: " + r.get("msg", ""))

    def _prod_identified(self, order: dict):
        """Gọi khi lệnh định danh có thêm pallet xong: đủ pallet thì lệnh sản xuất Hoàn thành."""
        key = order.get("prod_key")
        if key and order.get("status") == "COMPLETED":
            if self.prod.update_one({"key": key, "status": "IDENTIFYING"},
                                    {"$set": {"status": "DONE", "done_at": now(), "updated_at": now()}}).modified_count:
                self.event(f"Lệnh sản xuất {order.get('po_name')} định danh xong")

    def orders_import_preview(self, name: str, raw: bytes) -> dict:
        try:
            rows = F.read_orders(raw)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "msg": f"Không đọc được file lệnh sản xuất: {e}"}
        if not rows:
            return {"ok": False, "msg": "File không có lệnh nào"}
        groups: dict[str, dict] = {}
        have = {o["key"]: o["status"] for o in self.prod.find({}, {"key": 1, "status": 1})}
        prods = {p["item_code"] for p in self.products.find({}, {"item_code": 1})}
        for r in rows:
            st = r["status_text"] or "(trống)"
            g = groups.setdefault(st, {"status_text": st, "count": 0, "new": 0, "exists": 0, "busy": 0,
                                       "waiting": F.STATUS_IN.get(F._key(st)) == "WAITING"})
            g["count"] += 1
            cur = have.get(self.prod_key(r["code"], r["line"]))
            if cur is None:
                g["new"] += 1
            elif cur == "WAITING":
                g["exists"] += 1
            else:
                g["busy"] += 1
        miss = sorted({r["item_code"] for r in rows if F.STATUS_IN.get(F._key(r["status_text"])) == "WAITING"
                       and r["item_code"] and r["item_code"] not in prods})
        return {"ok": True, "token": self._stash({"kind": "orders", "rows": rows, "name": name}), "name": name,
                "total": len(rows), "groups": sorted(groups.values(), key=lambda g: -g["count"]), "missing_products": miss}

    def orders_import_apply(self, token: str, statuses: list[str]) -> dict:
        """Nạp các dòng có trạng thái được chọn (mặc định Kế hoạch / Đang sản xuất) vào B ở trạng thái Chờ định danh.
        Lệnh đã có (cùng mã + line) đang chờ thì cập nhật; đang định danh / xong thì giữ nguyên."""
        pend = self._pending.pop(token, None)
        if not pend or pend.get("kind") != "orders":
            return {"ok": False, "msg": "Phiên nạp file đã hết hạn, đọc lại file"}
        want = {F._key(x) for x in statuses}
        n_new = n_upd = n_skip = n_busy = 0
        for r in pend["rows"]:
            if F._key(r["status_text"] or "(trống)") not in want:
                n_skip += 1
                continue
            doc = self._prod_doc(r)
            key = self.prod_key(doc["code"], doc["line"])
            old = self.prod.find_one({"key": key})
            if old and old["status"] != "WAITING":
                n_busy += 1
                continue
            doc.update(key=key, status="WAITING", status_text=r["status_text"], source=pend["name"], updated_at=now())
            if old:
                self.prod.update_one({"_id": old["_id"]}, {"$set": doc})
                n_upd += 1
            else:
                self.prod.insert_one(dict(doc, created_at=now()))
                n_new += 1
            self._learn_carton_spec(doc)
        self.event(f"Nạp file lệnh sản xuất {pend['name']}: {n_new} mới, {n_upd} cập nhật, bỏ qua {n_skip + n_busy}")
        return {"ok": True, "msg": f"Đã nạp {n_new} lệnh mới, cập nhật {n_upd} lệnh chờ; bỏ qua {n_skip} dòng khác trạng thái"
                                   + (f", {n_busy} lệnh đang định danh / đã xong" if n_busy else "")}

    def _learn_carton_spec(self, doc: dict):
        """Sản phẩm chưa có quy cách thùng: lấy Tổng SL / Số kiện của lệnh sản xuất (nếu chia hết)."""
        ctn, qty = doc.get("ctn") or 0, doc.get("qty_total") or 0
        if ctn > 0 and qty > 0 and qty % ctn == 0:
            self.products.update_one({"item_code": doc["item_code"], "inner_carton": {"$lte": 0}},
                                     {"$set": {"inner_carton": int(qty // ctn), "updated_at": now()}})

    def orders_export(self, status: str = "") -> bytes:
        return F.write_orders(self.prod_list(status))

    def product_delete(self, code: str) -> dict:
        if self.pallets.count_documents({"item_code": code, "status": {"$in": list(PALLET_LIVE)}}):
            return {"ok": False, "msg": f"Còn pallet {code} trong kho - ngừng dùng (bỏ tick 'Dùng') thay vì xoá"}
        self.products.delete_one({"item_code": code})
        return {"ok": True, "msg": f"Đã xoá {code}"}

    # ------------------------------------------------------------------ lệnh: định danh
    def make_cartons(self, item_code: str, n: int, inner: int) -> list[str]:
        pre = self.cfg["codes"]["carton_prefix"]
        start = self.counters.find_one_and_update({"_id": "carton"}, {"$inc": {"v": n}}, upsert=True,
                                                  return_document=ReturnDocument.AFTER)["v"] - n
        return [f"{item_code}|||||||{inner}|0|{pre}{start + i + 1:012d}||001||||||||{(i % 99) + 1:02d}"
                for i in range(n)]

    def po_name(self) -> str:
        """VAS-ddmmyy-n: đánh số lại mỗi ngày như TKELog."""
        d = now().strftime("%d%m%y")
        return f"{self.cfg['codes']['po_prefix']}-{d}-{self.seq('po_name_' + d)}"

    def create_identify(self, kind: str, line_id: str, item_code: str, ctn_total: int, mfg_date: str = "",
                        dest_wh: str = "", po_name: str = "", prod_key: str = "") -> dict:
        """Lệnh định danh (đi đôi với lệnh sản xuất): sinh mã thùng, chia pallet theo quy cách, giữ chỗ ô cất."""
        prod = self.products.find_one({"item_code": item_code})
        if not prod:
            return {"ok": False, "msg": f"Chưa có sản phẩm {item_code} trong danh mục"}
        if line_id not in self.cfg["stations"]:
            return {"ok": False, "msg": f"Line {line_id} chưa khai điểm sàn (Cấu hình → Line)"}
        ctn_total = int(ctn_total)
        if ctn_total <= 0:
            return {"ok": False, "msg": "Số thùng phải > 0"}
        per = int(prod.get("inner_pallet") or 0)
        if per <= 0 or int(prod.get("inner_carton") or 0) <= 0:
            return {"ok": False, "msg": f"Sản phẩm {item_code} chưa có quy cách thùng / pallet (Danh mục → Sản phẩm)"}
        n_pal = math.ceil(ctn_total / per)
        dest_wh = dest_wh or self.store_wh()
        if not self.wh(dest_wh):
            return {"ok": False, "msg": f"Không có kho {dest_wh}"}
        free = self.locs.count_documents({"zone": dest_wh, "kind": "RACK", "state": L.EMPTY, "task": None})
        if free < n_pal:
            return {"ok": False, "msg": f"Kho {dest_wh} chỉ còn {free} ô trống, cần {n_pal}"}
        po_id = self.seq("po") + (190000000 if kind == "PO" else 180000000)
        name = po_name or (self.po_name() if kind == "PO" else f"ASN-{po_id}")
        order_id = f"{kind}{po_id}"
        wh_code = self.wh(dest_wh).get("wh_code") or "WH_TB"
        items, left = [], ctn_total
        for i in range(n_pal):
            n = min(per, left)
            left -= n
            pid = f"{self.cfg['codes']['pallet_prefix']}{self.seq('pallet'):05d}"
            loc = self.reserve_location(pid, dest_wh)
            clist = self.make_cartons(item_code, n, prod["inner_carton"])
            it = {"Item_Code": item_code, "Item_Name": prod["item_name"], "Pallet_ID": pid, "Location": loc,
                  "Carton_List": clist, "Ctn": n, "Qty": n * prod["inner_carton"], "Pcs": 0,
                  "Inner_Carton": prod["inner_carton"], "Inner_Pallet": per, "NW": prod.get("nw", 0), "GW": prod["gw"]}
            if kind == "PO":
                it["MFG_Date"] = mfg_date or now().strftime("%d/%m/%Y")
            items.append(it)
            self.pallets.insert_one({"pallet_id": pid, "item_code": item_code, "item_name": prod["item_name"],
                                     "cartons": clist, "shipped": [], "status": "PLANNED", "location": "", "wh": "",
                                     "assigned_location": loc, "dest_wh": dest_wh, "line_id": line_id,
                                     "order_id": order_id, "po_name": name, "mfg_date": it.get("MFG_Date", ""),
                                     "created_at": now()})
        if kind == "PO":
            body = {"PO_ID": po_id, "PO_Name": name, "WH_Code": wh_code, "Line_ID": line_id, "Pallet_Current": 1,
                    "Item_Information": items}
            path = "/identify-service/wms/task/identify-cartons"
        else:
            body = {"ASN_ID": po_id, "WH_Code": wh_code, "Line_ID": line_id, "Item_Information": items}
            path = "/identify-service/wms/task/identify-import"
        self.orders.insert_one({"order_id": order_id, "kind": kind, "po_id": po_id, "po_name": name,
                                "line_id": line_id, "item_code": item_code, "ctn_total": ctn_total, "dest_wh": dest_wh,
                                "pallets": [i["Pallet_ID"] for i in items], "done": [], "status": "SENT",
                                "prod_key": prod_key, "created_at": now()})
        self.send(path, body, order_id)
        self.event(f"Tạo lệnh định danh {name} ({kind} {po_id}) line {line_id}: {item_code} {ctn_total} thùng "
                   f"= {n_pal} pallet, cất kho {dest_wh}")
        return {"ok": True, "order_id": order_id, "po_id": po_id, "po_name": name, "pallets": n_pal, "body": body}

    def cancel_identify(self, po_id: int) -> dict:
        o = self.orders.find_one({"po_id": po_id})
        if not o:
            return {"ok": False, "msg": "không có lệnh"}
        self.send("/identify-service/wms/task/identify-cartons/cancel", {"PO_ID": po_id}, o["order_id"],
                  f"wcs:cancel:{po_id}:{self.seq('cancel')}")
        self.orders.update_one({"_id": o["_id"]}, {"$set": {"status": "CANCEL_SENT"}})
        for p in self.pallets.find({"order_id": o["order_id"], "status": "PLANNED"}):
            self._release_reserve(p["pallet_id"])
            self.pallets.update_one({"_id": p["_id"]}, {"$set": {"status": "CANCELLED", "assigned_location": ""}})
        return {"ok": True, "msg": "Đã gửi hủy, nhả ô đã giữ cho pallet chưa định danh"}

    # ------------------------------------------------------------------ lệnh: luân chuyển pallet (robot)
    def create_move(self, steps: list[dict], pallet_id: str, type_robot: int, type_move: int,
                    order_id: str | None = None) -> dict:
        """steps = [{"from": "...", "to": "..."}] -> Task_ID n/m_MovePallet_<ts> như TKELog."""
        order_id = order_id or f"MV{self.seq('move'):06d}"
        key = f"MovePallet_{now().strftime('%Y-%m-%d__%H:%M:%S.%f')}"
        tasks = []
        for i, s in enumerate(steps, 1):
            tasks.append({"Task_ID": f"{i}/{len(steps)}_{key}", "WH_Code": self.wh_code_of(s["to"]),
                          "Pallet_ID": pallet_id, "ASN_ID": 0, "DO_ID": 0, "PO_ID": 0,
                          "Type_Robot_ID": int(type_robot), "Type_Move_ID": int(type_move),
                          "Location_Old_ID": 0, "Location_New_ID": 0,
                          "Location_Old": L.norm(s["from"]), "Location_New": L.norm(s["to"])})
        self.orders.insert_one({"order_id": order_id, "kind": "MOVE", "pallet_id": pallet_id, "tasks": tasks,
                                "results": {}, "status": "SENT", "created_at": now()})
        for t in tasks:
            self.send("/move-service/task/wms/move_pallet", t, order_id, f"wcs:{t['Task_ID']}")
        self.event(f"Tạo lệnh luân chuyển {order_id} ({len(tasks)} bước) pallet {pallet_id}: "
                   + " → ".join([tasks[0]["Location_Old"]] + [t["Location_New"] for t in tasks]))
        return {"ok": True, "order_id": order_id, "tasks": tasks}

    def move_pallet(self, pallet_id: str, to: str, type_move: int = 11, type_robot: int = 0) -> dict:
        """Luân chuyển pallet đang có trong kho tới ô khác bằng robot."""
        p = self.pallets.find_one({"pallet_id": pallet_id})
        to = L.norm(to)
        if not p or not p.get("location"):
            return {"ok": False, "msg": f"Pallet {pallet_id} chưa có vị trí"}
        d = self.locs.find_one({"code": to})
        if not d:
            return {"ok": False, "msg": f"Không có ô {to}"}
        if d["kind"] == "RACK" and (d["state"] != L.EMPTY or d.get("task")):
            return {"ok": False, "msg": f"Ô {to} không trống ({d['state']})"}
        robot = int(type_robot or self.wh(d["zone"]).get("robot") or 1)
        self.locs.update_one({"code": to}, {"$set": {"task": f"reserve:{pallet_id}"}})
        return self.create_move([{"from": p["location"], "to": to}], pallet_id, robot, int(type_move))

    # ------------------------------------------------------------------ lệnh: cập nhật vị trí (tay, không robot)
    def update_location(self, pallet_id: str, to: str, note: str = "") -> dict:
        p = self.pallets.find_one({"pallet_id": pallet_id})
        if not p:
            return {"ok": False, "msg": f"Không có pallet {pallet_id}"}
        to = L.norm(to)
        if to:
            d = self.locs.find_one({"code": to})
            if not d:
                return {"ok": False, "msg": f"Không có ô {to}"}
            if d["state"] in (L.FULL, L.BUSY, L.LOCKED) and d.get("pallet_id") != pallet_id:
                return {"ok": False, "msg": f"Ô {to} đang {d['state']} ({d.get('pallet_id') or ''})"}
        old = p.get("location", "")
        self._place(pallet_id, to)
        st = "STORED" if to and p["status"] in ("PLANNED", "IDENTIFIED", "STORED") else p["status"]
        self.pallets.update_one({"pallet_id": pallet_id}, {"$set": {"status": st}})
        oid = f"UL{self.seq('upd'):06d}"
        self.orders.insert_one({"order_id": oid, "kind": "UPDATE_LOC", "pallet_id": pallet_id, "from": old, "to": to,
                                "note": note, "status": "COMPLETED", "created_at": now()})
        self.event(f"Cập nhật vị trí (tay) pallet {pallet_id}: {old or '-'} → {to or '(bỏ khỏi kệ)'} {note}")
        return {"ok": True, "msg": f"Pallet {pallet_id}: {old or '-'} → {to or '(không vị trí)'}", "order_id": oid}

    # ------------------------------------------------------------------ pallet / ô: sửa tay
    def pallet_create(self, b: dict) -> dict:
        """Tạo pallet tay (hàng có sẵn trong kho, chưa qua định danh của B) để quản lý / xuất hàng."""
        prod = self.products.find_one({"item_code": b.get("item_code")})
        if not prod:
            return {"ok": False, "msg": "Chọn sản phẩm"}
        ctn = int(b.get("ctn") or prod["inner_pallet"])
        pid = str(b.get("pallet_id") or f"{self.cfg['codes']['pallet_prefix']}{self.seq('pallet'):05d}").strip()
        loc = L.norm(b.get("location", ""))
        if loc:
            d = self.locs.find_one({"code": loc})
            if not d or d["state"] != L.EMPTY or d.get("task"):
                return {"ok": False, "msg": f"Ô {loc} không có hoặc không trống"}
        try:
            self.pallets.insert_one({"pallet_id": pid, "item_code": prod["item_code"], "item_name": prod["item_name"],
                                     "cartons": self.make_cartons(prod["item_code"], ctn, prod["inner_carton"]),
                                     "shipped": [], "status": "STORED" if loc else "IDENTIFIED", "location": "",
                                     "wh": "", "assigned_location": "", "order_id": "MANUAL",
                                     "mfg_date": b.get("mfg_date", ""), "created_at": now(), "manual": True})
        except DuplicateKeyError:
            return {"ok": False, "msg": f"Đã có pallet {pid}"}
        if loc:
            self._place(pid, loc)
        self.event(f"Tạo pallet tay {pid}: {prod['item_code']} {ctn} thùng tại {loc or '-'}")
        return {"ok": True, "msg": f"Đã tạo pallet {pid}", "pallet_id": pid}

    def pallet_update(self, b: dict) -> dict:
        pid = b.get("pallet_id")
        p = self.pallets.find_one({"pallet_id": pid})
        if not p:
            return {"ok": False, "msg": f"Không có pallet {pid}"}
        upd = {}
        if b.get("status") and b["status"] != p["status"]:
            upd["status"] = b["status"]
        if "mfg_date" in b:
            upd["mfg_date"] = b["mfg_date"]
        if upd:
            self.pallets.update_one({"_id": p["_id"]}, {"$set": upd})
        if "location" in b and L.norm(b["location"]) != (p.get("location") or ""):
            r = self.update_location(pid, b["location"], "sửa pallet")
            if not r["ok"]:
                return r
        self.event(f"Sửa tay pallet {pid}: {upd or ''}")
        return {"ok": True, "msg": f"Đã cập nhật pallet {pid}"}

    def pallet_delete(self, pid: str) -> dict:
        p = self.pallets.find_one({"pallet_id": pid})
        if not p:
            return {"ok": False, "msg": "Không có pallet"}
        if p.get("location"):
            self.locs.update_one({"code": p["location"], "pallet_id": pid},
                                 {"$set": {"state": L.EMPTY, "pallet_id": None, "task": None}})
        self._release_reserve(pid)
        self.pallets.delete_one({"_id": p["_id"]})
        self.event(f"Xoá tay pallet {pid} ({p.get('location') or '-'})")
        return {"ok": True, "msg": f"Đã xoá pallet {pid}"}

    def cell_set(self, code: str, state: str) -> dict:
        """Khoá / mở khoá / làm trống ô bằng tay. Làm trống ô có pallet = pallet không còn vị trí."""
        code = L.norm(code)
        d = self.locs.find_one({"code": code})
        if not d:
            return {"ok": False, "msg": f"Không có ô {code}"}
        if state not in (L.EMPTY, L.LOCKED):
            return {"ok": False, "msg": "Chỉ đổi được sang EMPTY hoặc LOCKED (đặt pallet: dùng Cập nhật vị trí)"}
        if d["state"] == L.BUSY:
            return {"ok": False, "msg": "Robot đang chở tới ô này"}
        if state == L.EMPTY and d.get("pallet_id"):
            self.pallets.update_one({"pallet_id": d["pallet_id"]}, {"$set": {"location": "", "wh": ""}})
        if state == L.LOCKED and d.get("pallet_id"):
            return {"ok": False, "msg": f"Ô có pallet {d['pallet_id']} - dời pallet trước khi khoá"}
        self.locs.update_one({"code": code}, {"$set": {"state": state, "pallet_id": None if state == L.EMPTY else
                                                        d.get("pallet_id"), "task": None if state == L.EMPTY else d.get("task")}})
        self.event(f"Sửa tay ô {code}: {d['state']} → {state}")
        return {"ok": True, "msg": f"Ô {code}: {state}"}

    # ------------------------------------------------------------------ lệnh: xuất hàng
    def create_export(self, belt: str, picks: list[dict]) -> dict:
        """picks = [{"pallet_id", "ctn_need"}]: lấy Carton_List còn trên pallet."""
        if not picks:
            return {"ok": False, "msg": "Chọn ít nhất 1 pallet"}
        do_id = self.seq("do") + 170000000
        items, wh_code = [], ""
        for i, pk in enumerate(picks, 1):
            p = self.pallets.find_one({"pallet_id": pk["pallet_id"]})
            if not p:
                return {"ok": False, "msg": f"không có pallet {pk['pallet_id']}"}
            left = [c for c in p["cartons"] if c not in p.get("shipped", [])]
            need = min(int(pk.get("ctn_need") or len(left)), len(left))
            wh_code = wh_code or self.wh_code_of(p.get("location", ""))
            items.append({"STT": i, "Pallet_ID": p["pallet_id"], "Item_Code": p["item_code"], "Ctn": len(left),
                          "Ctn_Need": need, "Carton_List": left})
        body = {"DO_ID": do_id, "WH_Code": wh_code or "WH_TB", "Conveyor_Belt": belt, "Item_Information": items}
        order_id = f"DO{do_id}"
        self.orders.insert_one({"order_id": order_id, "kind": "DO", "do_id": do_id, "line_id": belt,
                                "pallets": [i["Pallet_ID"] for i in items], "done": [], "status": "SENT",
                                "ctn_total": sum(i["Ctn_Need"] for i in items), "created_at": now()})
        self.send("/export-service/task/wms/export_items", body, order_id)
        self.event(f"Tạo lệnh xuất DO {do_id} băng {belt}: {len(items)} pallet, {body['Item_Information'] and sum(i['Ctn_Need'] for i in items)} thùng")
        return {"ok": True, "order_id": order_id, "do_id": do_id, "body": body}

    # ------------------------------------------------------------------ API TKELog (WCS gọi)
    def check_token(self, body: dict) -> bool:
        tok = ci_get(ci_get(body, "Auth", default={}) or {}, "Token", default="")
        return bool(tok) and self.tokens.find_one({"token": tok, "expire": {"$gt": now()}}) is not None

    def login(self, data: dict) -> dict:
        user, pw = ci_get(data, "User_Name", default=""), ci_get(data, "Password", default="")
        if self.cfg["users"].get(user) != pw:
            return tke(False, "Sai tên đăng nhập hoặc mật khẩu", code=1001)
        tok = secrets.token_hex(16)
        exp = now() + timedelta(minutes=self.cfg["token_minutes"])
        self.tokens.insert_one({"token": tok, "user": user, "expire": exp})
        return tke(True, data={"token_ID": tok, "token_Expired": exp.strftime("%d/%m/%Y %H:%M:%S")})

    def _injected_fail(self, api: str) -> bool:
        with self._lock:
            n = self.fail_next.get(api, 0)
            if n:
                self.fail_next[api] = n - 1
                return True
        return False

    def pallet_location(self, d: dict) -> dict:
        task_id = ci_get(d, "Task_ID", default="")
        ok = str(ci_get(d, "status", "Status", default="")).lower() in ("success", "true")
        pid = ci_get(d, "Pallet_ID", default="")
        new = L.norm(ci_get(d, "Location_New", default=""))
        old = L.norm(ci_get(d, "Location_Old", default=""))
        o = self.orders.find_one({"tasks.Task_ID": task_id})
        if o:
            self.orders.update_one({"_id": o["_id"]}, {"$set": {f"results.{task_id.replace('.', '_')}":
                                                                "success" if ok else "false"}})
            o = self.orders.find_one({"_id": o["_id"]})
            res = list(o["results"].values())
            if "false" in res:
                st = "FAILED"
            elif len(res) == len(o["tasks"]):
                st = "COMPLETED"
            else:
                st = "RUNNING"
            self.orders.update_one({"_id": o["_id"]}, {"$set": {"status": st}})
        if ok:
            if self.pallets.find_one({"pallet_id": pid}):
                self._place(pid, new)
                self.pallets.update_one({"pallet_id": pid, "status": {"$in": ["PLANNED", "IDENTIFIED"]}},
                                        {"$set": {"status": "STORED"}})
            else:   # pallet chưa có trong WMS (vd. move tay của WCS) - chỉ cập nhật ô
                self.locs.update_one({"code": old, "kind": "RACK"}, {"$set": {"state": L.EMPTY, "pallet_id": None}})
                self.locs.update_one({"code": new}, {"$set": {"state": L.FULL, "pallet_id": pid, "task": None}})
        else:
            self.locs.update_many({"code": new, "task": f"reserve:{pid}", "state": L.EMPTY}, {"$set": {"task": None}})
        self.event(f"Move {task_id} pallet {pid} {old}->{new}: {'OK' if ok else 'LỖI'}")
        return tke(True)

    def finished_goods(self, d: dict) -> dict:
        if self._injected_fail("Finished_Goods"):
            return None  # -> HTTP 503 (lỗi tạm thời, WCS phải gửi lại)
        pid = ci_get(d, "Pallet_ID", default="")
        p = self.pallets.find_one({"pallet_id": pid})
        if not p:
            return tke(False, f"Pallet {pid} không tồn tại", code=210)
        po = ci_get(d, "PO_ID", "ASN_ID")
        ctn = int(ci_get(d, "Ctn", default=0) or 0)
        line = ci_get(d, "Line_ID", default="")
        station = L.norm(self.cfg["stations"].get(line, ""))
        first = p["status"] == "PLANNED"
        upd = {"status": "IDENTIFIED", "ctn_done": ctn, "location": station, "identified_at": now()}
        if 0 < ctn < len(p["cartons"]):   # pallet lẻ: trạm chốt sớm (hủy giữa chừng) -> chỉ giữ số thùng thực đạt
            upd["cartons"] = p["cartons"][:ctn]
            upd["cartons_void"] = p["cartons"][ctn:]
        self.pallets.update_one({"_id": p["_id"]}, {"$set": upd})
        o = self.orders.find_one_and_update({"po_id": po}, {"$addToSet": {"done": pid}},
                                            return_document=ReturnDocument.AFTER)
        if o:
            st = "COMPLETED" if set(o["pallets"]) <= set(o["done"]) else "RUNNING"
            if o["status"] in ("CANCEL_SENT",):
                st = o["status"]
            self.orders.update_one({"_id": o["_id"]}, {"$set": {"status": st}})
            self._prod_identified(dict(o, status=st))
        self.event(f"Pallet {pid} định danh xong ({ctn} thùng) tại {line}")
        loc = p.get("assigned_location") or ""
        if first and self.cfg["auto_putaway"] and station and loc:
            robot = int(self.wh(self.locstore.wh.of(loc)).get("robot") or 1)
            self.create_move([{"from": station, "to": loc}], pid, robot, 3)
        return tke(True, data={"Pallet_ID": pid, "Location": loc})

    def ob_pallet(self, d: dict) -> dict:
        do_id = ci_get(d, "DO_ID")
        for it in ci_get(d, "Item_Information", default=[]) or []:
            pid = ci_get(it, "Pallet_ID", default="")
            shipped = ci_get(it, "Carton_List", default=[]) or []
            p = self.pallets.find_one_and_update({"pallet_id": pid}, {"$addToSet": {"shipped": {"$each": shipped}}},
                                                 return_document=ReturnDocument.AFTER)
            if p and set(p["cartons"]) <= set(p["shipped"]):
                self.pallets.update_one({"_id": p["_id"]}, {"$set": {"status": "SHIPPED"}})
                if p.get("location"):
                    self.locs.update_one({"code": p["location"], "pallet_id": pid},
                                         {"$set": {"state": L.EMPTY, "pallet_id": None}})
            self.orders.update_one({"do_id": do_id}, {"$addToSet": {"done": pid}})
            self.event(f"DO {do_id} xuất pallet {pid}: {len(shipped)} thùng")
        o = self.orders.find_one({"do_id": do_id})
        if o and set(o["pallets"]) <= set(o["done"]):
            self.orders.update_one({"_id": o["_id"]}, {"$set": {"status": "COMPLETED"}})
        return tke(True)

    def location_stocks(self, d: dict) -> dict:
        loc = L.norm(ci_get(d, "Location", default=""))
        x = self.locs.find_one({"code": loc})
        mine = f"reserve:{ci_get(d, 'Pallet_ID', default='')}"   # ô đang giữ chỗ cho chính pallet hỏi -> coi là trống
        occupied = (not x) or x["state"] in (L.FULL, L.BUSY, L.LOCKED) or bool(x.get("task") and x["task"] != mine)
        # Status = true: có hàng / không dùng được; false: ô trống -> WCS được đổi đích / đưa pallet vào
        return tke(True, data={"Task_ID": ci_get(d, "Task_ID", default=""), "Location_ID": 0, "Location": loc,
                               "Status": occupied})

    # ------------------------------------------------------------------ cấu hình / dashboard
    EDITABLE = ("wcs", "users", "token_minutes", "locations", "stations", "auto_putaway", "codes", "outbox",
                "messages_keep_days")

    def config_view(self) -> dict:
        c = {k: copy.deepcopy(self.cfg[k]) for k in self.EDITABLE}
        return {"config": c, "server": self.cfg["server"], "mongo": self.cfg["mongo"], "file": self.cfg["_path"]}

    def config_save(self, new: dict) -> dict:
        errs = []
        if not str(new.get("wcs", {}).get("url", "")).startswith("http"):
            errs.append("wcs.url phải bắt đầu bằng http")
        users = new.get("users", {})
        if not isinstance(users, dict) or not users or any(not k or not v for k, v in users.items()):
            errs.append("Cần ít nhất 1 tài khoản có tên + mật khẩu")
        st = new.get("stations", {})
        if not isinstance(st, dict) or any(not k for k in st):
            errs.append("stations: line_id không được trống")
        if errs:
            return {"ok": False, "msg": "; ".join(errs)}
        new["stations"] = {k.strip(): L.norm(v) for k, v in st.items()}
        merged = deep_merge(self.cfg, {k: new[k] for k in self.EDITABLE if k in new})
        for k in ("users", "stations"):  # dict thay hẳn (cho phép xoá dòng)
            merged[k] = new[k]
        self.cfg.clear()
        self.cfg.update(merged)
        save_config(self.cfg)
        self.outbox.retry_s = float(self.cfg["outbox"]["retry_s"])
        self.outbox.timeout_s = float(self.cfg["outbox"]["timeout_s"])
        self.locstore.load()  # điểm sàn có thể đã đổi
        log.info("Lưu cấu hình từ giao diện -> %s", self.cfg["_path"])
        return {"ok": True, "msg": f"Đã lưu {self.cfg['_path']} và áp dụng"}

    def inventory(self) -> list[dict]:
        """Tồn theo kho × sản phẩm: số pallet, số thùng còn."""
        out = list(self.pallets.aggregate([
            {"$match": {"status": {"$in": ["IDENTIFIED", "STORED"]}}},
            {"$project": {"wh": 1, "item_code": 1, "left": {"$subtract": [{"$size": "$cartons"},
                                                                         {"$size": {"$ifNull": ["$shipped", []]}}]}}},
            {"$group": {"_id": {"wh": "$wh", "item": "$item_code"}, "pallets": {"$sum": 1}, "cartons": {"$sum": "$left"}}},
            {"$sort": {"_id.wh": 1, "_id.item": 1}}]))
        return [{"wh": r["_id"]["wh"] or "-", "item_code": r["_id"]["item"], "pallets": r["pallets"],
                 "cartons": r["cartons"]} for r in out]

    def dashboard(self) -> dict:
        t0 = today_start()
        kinds = {r["_id"]: r["n"] for r in self.orders.aggregate([
            {"$match": {"created_at": {"$gte": t0}}}, {"$group": {"_id": "$kind", "n": {"$sum": 1}}}])}
        pst = {r["_id"]: r["n"] for r in self.pallets.aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}])}
        occ: dict = {}
        for r in self.locs.aggregate([{"$match": {"kind": "RACK"}},
                                      {"$group": {"_id": {"z": "$zone", "s": "$state"}, "n": {"$sum": 1}}}]):
            occ.setdefault(r["_id"]["z"] or "-", {})[r["_id"]["s"]] = r["n"]
        zone = self.store_wh()
        z = occ.get(zone, {})
        reserved = self.locs.count_documents({"zone": zone, "state": L.EMPTY, "task": {"$ne": None}})
        ob = self.db["outbox"]
        names = {w["code"]: w["name"] for w in self.locstore.wh.all()}
        return clean({
            "now": now(), "store_zone": zone, "wh_names": names,
            "tiles": {
                "orders_today": sum(kinds.values()), "orders_by_kind": kinds,
                "orders_running": self.orders.count_documents({"status": {"$in": ["SENT", "RUNNING", "CANCEL_SENT"]}}),
                "orders_failed": self.orders.count_documents({"status": {"$in": ["FAILED", "REJECTED"]},
                                                              "created_at": {"$gte": t0}}),
                "pallets": pst, "cartons_in_stock": sum(r["cartons"] for r in self.inventory()),
                "zone_total": sum(z.values()), "zone_full": z.get(L.FULL, 0), "zone_reserved": reserved,
                "zone_free": max(z.get(L.EMPTY, 0) - reserved, 0),
                "outbox_pending": ob.count_documents({"status": {"$in": ["PENDING", "SENDING"]}}),
                "outbox_dead": ob.count_documents({"status": "DEAD"}),
            },
            "hourly_orders": hourly(self.orders, "created_at", {}),
            "hourly_identified": hourly(self.pallets, "identified_at", {}),
            "occupancy": occ, "inventory": self.inventory(),
            "events": list(self.events.find().sort("t", DESCENDING).limit(8)),
        })



def site_redirect(req: Request) -> RedirectResponse:
    """Giao diện đã gom về site chung (python -m g8b.web, cùng máy, cổng theo config/web.json)."""
    return RedirectResponse(f"http://{req.url.hostname}:{site_port()}/")


def build_app(w: WMS) -> FastAPI:
    app = FastAPI(title="G8 WMS ảo", version=VERSION)
    app.mount("/static", StaticFiles(directory=os.path.join(ROOT, "g8b", "static")), name="static")
    static = os.path.join(ROOT, "g8b", "static")

    async def tkel(req: Request, fn, need_token: bool = True):
        try:
            body = await req.json()
        except Exception:
            body = {}
        w.msg.add("IN", req.client.host if req.client else "", req.url.path,
                  {"Data": body.get("Data") if isinstance(body, dict) else body})
        if need_token and not w.check_token(body):
            resp = tke(False, "Token không hợp lệ hoặc hết hạn", code=203)
        else:
            resp = fn(ci_get(body, "Data", default={}) or {})
        if resp is None:
            w.msg.add("ACK", "WCS", req.url.path, None, 503, "lỗi giả lập")
            return JSONResponse({"error": "simulated outage"}, status_code=503)
        w.msg.add("ACK", "WCS", req.url.path, None, 200, resp)
        return JSONResponse(resp)

    async def body(req: Request) -> dict:
        try:
            b = await req.json()
        except Exception:
            b = {}
        return b if isinstance(b, dict) else {}

    @app.post("/API/General/Sys/Login")
    async def api_login(req: Request):
        return await tkel(req, w.login, need_token=False)

    @app.post("/API/G8/Customer/Update/Pallet_Location")
    async def api_pl(req: Request):
        return await tkel(req, w.pallet_location)

    @app.post("/API/G8/Customer/Update/Finished_Goods")
    async def api_fg(req: Request):
        return await tkel(req, w.finished_goods)

    @app.post("/API/G8/Customer/Update/OB_Pallet")
    async def api_ob(req: Request):
        return await tkel(req, w.ob_pallet)

    @app.post("/API/G8/Customer/Update/Location_Stocks")
    async def api_ls(req: Request):
        return await tkel(req, w.location_stocks)

    # --- giao diện
    @app.get("/")
    def ui(req: Request):
        return site_redirect(req)

    @app.get("/api/config")
    def api_cfg():
        return {"stations": w.cfg["stations"], "version": VERSION, "store_zone": w.store_wh(), "prod_status": PROD_STATUS,
                "warehouses": w.locstore.wh.all(), "type_move": TYPE_MOVE, "robots": ROBOTS}

    @app.post("/api/identify")
    async def ui_identify(req: Request):
        b = await body(req)
        try:
            return w.create_identify(b.get("kind", "PO"), b.get("line_id", ""), b.get("item_code", ""),
                                     int(b.get("ctn_total") or 0), b.get("mfg_date", ""), b.get("dest_wh", ""),
                                     b.get("po_name", ""))
        except (TypeError, ValueError) as e:
            return {"ok": False, "msg": str(e)}

    @app.post("/api/identify/cancel")
    async def ui_identify_cancel(req: Request):
        b = await body(req)
        return w.cancel_identify(int(b["po_id"]))

    @app.post("/api/move")
    async def ui_move(req: Request):
        """Luân chuyển: {pallet_id, to, type_move, type_robot?} hoặc nhiều bước {pallet_id, steps:[{from,to}]}."""
        b = await body(req)
        if b.get("steps"):
            return w.create_move(b["steps"], b.get("pallet_id") or f"LPNB-M{w.seq('mp'):04d}",
                                 int(b.get("type_robot") or 1), int(b.get("type_move") or 11))
        return w.move_pallet(b.get("pallet_id", ""), b.get("to", ""), int(b.get("type_move") or 11),
                             int(b.get("type_robot") or 0))

    @app.post("/api/update_location")
    async def ui_update_location(req: Request):
        b = await body(req)
        return w.update_location(b.get("pallet_id", ""), b.get("to", ""), b.get("note", ""))

    @app.post("/api/export")
    async def ui_export(req: Request):
        b = await body(req)
        return w.create_export(b.get("belt", ""), b.get("picks") or [])

    @app.get("/api/products")
    def ui_products():
        return clean(list(w.products.find().sort("item_code", ASCENDING)))

    @app.post("/api/products")
    async def ui_product_save(req: Request):
        return w.product_save(await body(req))

    @app.post("/api/products/delete")
    async def ui_product_delete(req: Request):
        return w.product_delete((await body(req)).get("item_code", ""))

    def xlsx(data: bytes, name: str) -> Response:
        return Response(data, headers={"Content-Disposition": f'attachment; filename="{name}"'},
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def b64(b: dict) -> bytes:
        return base64.b64decode((b.get("b64") or "").split(",")[-1])

    def stamp() -> str:
        return now().strftime("%y%m%d%H%M%S")

    @app.get("/api/products/export")
    def ui_products_export():
        return xlsx(w.products_export(), f"G8B_San_Pham_{stamp()}.xlsx")

    @app.post("/api/products/import/preview")
    async def ui_products_preview(req: Request):
        b = await body(req)
        return w.products_import_preview(b.get("name", ""), b64(b))

    @app.post("/api/products/import/apply")
    async def ui_products_apply(req: Request):
        return w.products_import_apply((await body(req)).get("token", ""))

    @app.get("/api/prod")
    def ui_prod(status: str = "", q: str = ""):
        return w.prod_list(status, q)

    @app.post("/api/prod")
    async def ui_prod_save(req: Request):
        return w.prod_save(await body(req))

    @app.post("/api/prod/delete")
    async def ui_prod_delete(req: Request):
        return w.prod_delete((await body(req)).get("key", ""))

    @app.post("/api/prod/start")
    async def ui_prod_start(req: Request):
        b = await body(req)
        try:
            return w.prod_start(b.get("key", ""), b.get("line_id", ""), int(b.get("ctn") or 0), b.get("mfg_date", ""),
                                b.get("dest_wh", ""))
        except (TypeError, ValueError) as e:
            return {"ok": False, "msg": str(e)}

    @app.post("/api/prod/cancel")
    async def ui_prod_cancel(req: Request):
        return w.prod_cancel((await body(req)).get("key", ""))

    @app.get("/api/prod/export")
    def ui_prod_export(status: str = ""):
        return xlsx(w.orders_export(status), f"G8B_San_Xuat_{stamp()}.xlsx")

    @app.post("/api/prod/import/preview")
    async def ui_prod_preview(req: Request):
        b = await body(req)
        return w.orders_import_preview(b.get("name", ""), b64(b))

    @app.post("/api/prod/import/apply")
    async def ui_prod_apply(req: Request):
        b = await body(req)
        return w.orders_import_apply(b.get("token", ""), b.get("statuses") or [])

    @app.post("/api/pallets/create")
    async def ui_pallet_create(req: Request):
        return w.pallet_create(await body(req))

    @app.post("/api/pallets/update")
    async def ui_pallet_update(req: Request):
        return w.pallet_update(await body(req))

    @app.post("/api/pallets/delete")
    async def ui_pallet_delete(req: Request):
        return w.pallet_delete((await body(req)).get("pallet_id", ""))

    @app.get("/api/pallets/{pallet_id}")
    def ui_pallet(pallet_id: str):
        return clean(w.pallets.find_one({"pallet_id": pallet_id}) or {})

    @app.get("/api/inventory")
    def ui_inventory():
        return w.inventory()

    @app.get("/api/dashboard")
    def ui_dashboard():
        return w.dashboard()

    @app.get("/api/settings")
    def ui_settings():
        return w.config_view()

    @app.put("/api/settings")
    async def ui_settings_save(req: Request):
        try:
            return w.config_save(await req.json())
        except Exception as e:
            log.exception("lưu cấu hình lỗi")
            return {"ok": False, "msg": str(e)}

    L.add_routes(app, w.locstore, lambda: save_config(w.cfg))

    @app.get("/api/orders")
    def ui_orders(limit: int = 300, kind: str = ""):
        q = {"kind": kind} if kind else {}
        return clean(list(w.orders.find(q).sort("created_at", DESCENDING).limit(limit)))

    @app.get("/api/pallets")
    def ui_pallets(limit: int = 1000, status: str = "", wh: str = "", q: str = ""):
        f: dict = {}
        if status:
            f["status"] = {"$in": status.split(",")}
        if wh:
            f["wh"] = wh
        if q:
            f["$or"] = [{"pallet_id": {"$regex": re.escape(q), "$options": "i"}},
                        {"item_code": {"$regex": re.escape(q), "$options": "i"}},
                        {"location": {"$regex": "^" + re.escape(L.norm(q))}}]
        out = clean(list(w.pallets.find(f).sort("created_at", DESCENDING).limit(limit)))
        for p in out:
            p["ctn_total"] = len(p.pop("cartons", []))
            p["ctn_shipped"] = len(p.pop("shipped", []))
            p.pop("cartons_void", None)
        return out

    @app.get("/api/locations")
    def ui_locations(wh: str = ""):
        q = {"zone": wh} if wh else {}
        return clean(list(w.locs.find(q, {"hb": 0}).sort("code", ASCENDING).limit(6000)))

    @app.post("/api/locations/set")
    async def ui_loc_set(req: Request):
        b = await body(req)
        return w.cell_set(b.get("code", ""), str(b.get("state", "")).upper())

    @app.get("/api/events")
    def ui_events(limit: int = 300):
        return clean(list(w.events.find().sort("t", DESCENDING).limit(limit)))

    @app.get("/api/messages")
    def ui_messages(limit: int = 300):
        return clean(list(w.db["messages"].find().sort("t", DESCENDING).limit(limit)))

    @app.get("/api/outbox")
    def ui_outbox(limit: int = 200):
        return clean(list(w.db["outbox"].find().sort("created_at", DESCENDING).limit(limit)))

    @app.post("/api/test/fail_next")
    async def ui_fail_next(req: Request):
        b = await body(req)
        with w._lock:
            w.fail_next.update(b)
        return {"ok": True, "fail_next": w.fail_next}

    return app


def main():
    cfg = load_config("wms", DEFAULTS)
    setup_logging("wms", os.environ.get("G8B_LOG_DIR") and os.path.join(os.environ["G8B_LOG_DIR"], "wms"))
    log.info("WMS ảo v%s - cấu hình %s", VERSION, cfg["_path"])
    w = WMS(cfg)
    w.outbox.start()
    w.locstore.watch()
    uvicorn.run(build_app(w), host=cfg["server"]["host"], port=cfg["server"]["port"], log_level="warning")


if __name__ == "__main__":
    main()
