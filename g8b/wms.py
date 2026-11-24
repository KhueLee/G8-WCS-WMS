"""WMS ảo - nói đúng API TKELog WMS V5 (bản WCS gốc đang dùng).

WCS -> WMS: Login, Pallet_Location, Finished_Goods, OB_Pallet, Location_Stocks (định dạng {Auth, Data}).
WMS -> WCS: move_pallet, identify-cartons, identify-import, identify-cartons/cancel, export_items
(gửi qua outbox, WCS trả {"code":200}).
Chạy: python -m g8b.wms
"""
from __future__ import annotations

import logging
import os
import random
import secrets
import threading
from datetime import timedelta
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pymongo import ASCENDING, DESCENDING, ReturnDocument

from . import locations as L
from .common import MsgLog, ROOT, ci_get, clean, load_config, mongo_db, now, setup_logging
from .outbox import Outbox

VERSION = "0.1.0"

DEFAULTS: dict = {
    "server": {"host": "0.0.0.0", "port": 9100},
    "mongo": {"uri": "mongodb://127.0.0.1:27017", "db": "g8b_wms"},
    "wcs": {"url": "http://127.0.0.1:9000"},
    "wh_code": "WH_TB",
    "users": {"WCS_API_User": "123456"},
    "token_minutes": 480,
    "locations": {"file": "config/locations.json", "store_zone": "B"},
    # vị trí sàn của từng line định danh / băng tải xuất (điểm robot lấy / trả pallet)
    "stations": {},
    "auto_putaway": True,       # pallet định danh xong -> tự tạo lệnh Move cất kệ
    "putaway_robot": 1,         # 1 = xe nâng, 2 = robot đội kệ
    "outbox": {"retry_s": 5, "timeout_s": 10},
    "messages_keep_days": 14,
    "fail_next": {},            # dùng khi test: {"Finished_Goods": 1} -> từ chối 1 lần
}

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
        self.locs = self.db["locations"]
        self.tokens = self.db["tokens"]
        self.counters = self.db["counters"]
        self.events = self.db["events"]
        self._lock = threading.Lock()
        self.fail_next = dict(cfg.get("fail_next", {}))
        self.tokens.create_index("token", unique=True)
        self.tokens.create_index("expire", expireAfterSeconds=0)
        self.orders.create_index("order_id", unique=True)
        self.orders.create_index("created_at")
        self.pallets.create_index("pallet_id", unique=True)
        self.events.create_index("t", expireAfterSeconds=30 * 86400)
        f = cfg["locations"].get("file")
        if f and os.path.exists(f if os.path.isabs(f) else os.path.join(ROOT, f)):
            log.info("Nạp vị trí: %s", L.sync_collection(self.locs, L.load_file(f)))

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

    def reserve_location(self, pallet_id: str) -> str:
        """Chọn ô trống trong zone lưu trữ của B và giữ chỗ."""
        d = self.locs.find_one_and_update(
            {"zone": self.cfg["locations"]["store_zone"], "kind": "RACK", "state": L.EMPTY, "task": None},
            {"$set": {"task": f"reserve:{pallet_id}"}}, sort=[("code", ASCENDING)])
        return d["code"] if d else ""

    # ------------------------------------------------------------------ tạo lệnh (giao diện / API)
    def create_move(self, steps: list[dict], pallet_id: str, type_robot: int, type_move: int,
                    order_id: str | None = None) -> dict:
        """steps = [{"from": "...", "to": "..."}] -> Task_ID n/m_MovePallet_<ts> như TKELog."""
        order_id = order_id or f"MV{self.seq('move'):06d}"
        key = f"MovePallet_{now().strftime('%Y-%m-%d__%H:%M:%S.%f')}"
        tasks = []
        for i, s in enumerate(steps, 1):
            tasks.append({"Task_ID": f"{i}/{len(steps)}_{key}", "WH_Code": self.cfg["wh_code"],
                          "Pallet_ID": pallet_id, "ASN_ID": 0, "DO_ID": 0, "PO_ID": 0,
                          "Type_Robot_ID": int(type_robot), "Type_Move_ID": int(type_move),
                          "Location_Old_ID": 0, "Location_New_ID": 0,
                          "Location_Old": L.norm(s["from"]), "Location_New": L.norm(s["to"])})
        self.orders.insert_one({"order_id": order_id, "kind": "MOVE", "pallet_id": pallet_id, "tasks": tasks,
                                "results": {}, "status": "SENT", "created_at": now()})
        for t in tasks:
            self.send("/move-service/task/wms/move_pallet", t, order_id, f"wcs:{t['Task_ID']}")
        self.event(f"Tạo lệnh Move {order_id} ({len(tasks)} bước) pallet {pallet_id}")
        return {"ok": True, "order_id": order_id, "tasks": tasks}

    def make_cartons(self, item_code: str, n: int, inner: int) -> list[str]:
        out = []
        for _ in range(n):
            code = f"G8B{self.seq('carton'):012d}"
            out.append(f"{item_code}|||||||{inner}|0|{code}||001||||||||01")
        return out

    def create_identify(self, kind: str, line_id: str, item_code: str, item_name: str, pallets: int,
                        cartons: int, inner: int, gw: float, mfg_date: str = "") -> dict:
        po_id = self.seq("po") + (190000000 if kind == "PO" else 180000000)
        order_id = f"{kind}{po_id}"
        items = []
        for i in range(pallets):
            pid = f"LPNB{self.seq('pallet'):05d}"
            loc = self.reserve_location(pid)
            clist = self.make_cartons(item_code, cartons, inner)
            it = {"Item_Code": item_code, "Item_Name": item_name, "Pallet_ID": pid, "Location": loc,
                  "Carton_List": clist, "Ctn": cartons, "Qty": cartons * inner, "Pcs": 0,
                  "Inner_Carton": inner, "Inner_Pallet": cartons, "NW": 0, "GW": gw}
            if kind == "PO":
                it["MFG_Date"] = mfg_date or now().strftime("%d/%m/%Y")
            items.append(it)
            self.pallets.insert_one({"pallet_id": pid, "item_code": item_code, "item_name": item_name,
                                     "cartons": clist, "shipped": [], "status": "PLANNED", "location": "",
                                     "assigned_location": loc, "line_id": line_id, "order_id": order_id,
                                     "created_at": now()})
        if kind == "PO":
            body = {"PO_ID": po_id, "PO_Name": f"VB-{now().strftime('%d%m%y')}-{po_id % 1000}",
                    "WH_Code": self.cfg["wh_code"], "Line_ID": line_id, "Pallet_Current": 1,
                    "Item_Information": items}
            path = "/identify-service/wms/task/identify-cartons"
        else:
            body = {"ASN_ID": po_id, "WH_Code": self.cfg["wh_code"], "Line_ID": line_id, "Item_Information": items}
            path = "/identify-service/wms/task/identify-import"
        self.orders.insert_one({"order_id": order_id, "kind": kind, "po_id": po_id, "line_id": line_id,
                                "pallets": [i["Pallet_ID"] for i in items], "done": [], "status": "SENT",
                                "created_at": now()})
        self.send(path, body, order_id)
        self.event(f"Tạo lệnh {kind} {po_id} line {line_id}: {pallets} pallet x {cartons} thùng")
        return {"ok": True, "order_id": order_id, "po_id": po_id, "body": body}

    def cancel_identify(self, po_id: int) -> dict:
        o = self.orders.find_one({"po_id": po_id})
        if not o:
            return {"ok": False, "msg": "không có lệnh"}
        self.send("/identify-service/wms/task/identify-cartons/cancel", {"PO_ID": po_id}, o["order_id"],
                  f"wcs:cancel:{po_id}:{self.seq('cancel')}")
        self.orders.update_one({"_id": o["_id"]}, {"$set": {"status": "CANCEL_SENT"}})
        return {"ok": True}

    def create_export(self, belt: str, picks: list[dict]) -> dict:
        """picks = [{"pallet_id", "ctn_need"}]: lấy Carton_List còn trên pallet."""
        do_id = self.seq("do") + 170000000
        items = []
        for i, pk in enumerate(picks, 1):
            p = self.pallets.find_one({"pallet_id": pk["pallet_id"]})
            if not p:
                return {"ok": False, "msg": f"không có pallet {pk['pallet_id']}"}
            left = [c for c in p["cartons"] if c not in p.get("shipped", [])]
            need = min(int(pk.get("ctn_need") or len(left)), len(left))
            items.append({"STT": i, "Pallet_ID": p["pallet_id"], "Item_Code": p["item_code"], "Ctn": len(left),
                          "Ctn_Need": need, "Carton_List": left})
        body = {"DO_ID": do_id, "WH_Code": self.cfg["wh_code"], "Conveyor_Belt": belt, "Item_Information": items}
        order_id = f"DO{do_id}"
        self.orders.insert_one({"order_id": order_id, "kind": "DO", "do_id": do_id, "line_id": belt,
                                "pallets": [i["Pallet_ID"] for i in items], "done": [], "status": "SENT",
                                "created_at": now()})
        self.send("/export-service/task/wms/export_items", body, order_id)
        self.event(f"Tạo lệnh xuất DO {do_id} băng {belt}: {len(items)} pallet")
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
            self.locs.update_one({"code": old, "kind": "RACK"}, {"$set": {"state": L.EMPTY, "pallet_id": None,
                                                                         "task": None}})
            self.locs.update_one({"code": new}, {"$set": {"state": L.FULL, "pallet_id": pid, "task": None}})
            self.pallets.update_one({"pallet_id": pid}, {"$set": {"location": new, "status": "STORED"}})
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
        self.pallets.update_one({"_id": p["_id"]}, {"$set": {"status": "IDENTIFIED", "ctn_done": ctn,
                                                             "location": station, "identified_at": now()}})
        o = self.orders.find_one_and_update({"po_id": po}, {"$addToSet": {"done": pid}},
                                            return_document=ReturnDocument.AFTER)
        if o:
            st = "COMPLETED" if set(o["pallets"]) <= set(o["done"]) else "RUNNING"
            if o["status"] in ("CANCEL_SENT",):
                st = o["status"]
            self.orders.update_one({"_id": o["_id"]}, {"$set": {"status": st}})
        self.event(f"Pallet {pid} định danh xong ({ctn} thùng) tại {line}")
        if first and self.cfg["auto_putaway"] and station and p.get("assigned_location"):
            self.create_move([{"from": station, "to": p["assigned_location"]}], pid,
                             self.cfg["putaway_robot"], 3)
        return tke(True, data={"Pallet_ID": pid, "Location": p.get("assigned_location", "")})

    def ob_pallet(self, d: dict) -> dict:
        do_id = ci_get(d, "DO_ID")
        for it in ci_get(d, "Item_Information", default=[]) or []:
            pid = ci_get(it, "Pallet_ID", default="")
            shipped = ci_get(it, "Carton_List", default=[]) or []
            p = self.pallets.find_one_and_update({"pallet_id": pid}, {"$addToSet": {"shipped": {"$each": shipped}}},
                                                 return_document=ReturnDocument.AFTER)
            if p and set(p["cartons"]) <= set(p["shipped"]):
                self.pallets.update_one({"_id": p["_id"]}, {"$set": {"status": "SHIPPED"}})
            self.orders.update_one({"do_id": do_id}, {"$addToSet": {"done": pid}})
            self.event(f"DO {do_id} xuất pallet {pid}: {len(shipped)} thùng")
        o = self.orders.find_one({"do_id": do_id})
        if o and set(o["pallets"]) <= set(o["done"]):
            self.orders.update_one({"_id": o["_id"]}, {"$set": {"status": "COMPLETED"}})
        return tke(True)

    def location_stocks(self, d: dict) -> dict:
        loc = L.norm(ci_get(d, "Location", default=""))
        x = self.locs.find_one({"code": loc})
        occupied = (not x) or x["state"] in (L.FULL, L.BUSY, L.LOCKED) or bool(x.get("task"))
        # Status = false: ô trống -> chấp nhận (như WCS gốc hiểu)
        return tke(True, data={"Task_ID": ci_get(d, "Task_ID", default=""), "Location_ID": 0, "Location": loc,
                               "Status": occupied})


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
    def ui():
        return FileResponse(os.path.join(static, "wms.html"))

    @app.get("/api/config")
    def api_cfg():
        return {"stations": w.cfg["stations"], "wh_code": w.cfg["wh_code"], "version": VERSION,
                "store_zone": w.cfg["locations"]["store_zone"]}

    @app.post("/api/move")
    async def ui_move(req: Request):
        b = await req.json()
        steps = b.get("steps") or [{"from": b.get("from"), "to": b.get("to")}]
        return w.create_move(steps, b.get("pallet_id") or f"LPNB-M{w.seq('mp'):04d}", b.get("type_robot", 1),
                             b.get("type_move", 11))

    @app.post("/api/identify")
    async def ui_identify(req: Request):
        b = await req.json()
        return w.create_identify(b.get("kind", "PO"), b["line_id"], b.get("item_code", "TRU_G01_50W_AT"),
                                 b.get("item_name", "Bóng Led trụ nhôm T50 (mô phỏng)"), int(b.get("pallets", 1)),
                                 int(b.get("cartons", 6)), int(b.get("inner", 6)), float(b.get("gw", 1.637)),
                                 b.get("mfg_date", ""))

    @app.post("/api/identify/cancel")
    async def ui_identify_cancel(req: Request):
        b = await req.json()
        return w.cancel_identify(int(b["po_id"]))

    @app.post("/api/export")
    async def ui_export(req: Request):
        b = await req.json()
        return w.create_export(b["belt"], b["picks"])

    @app.get("/api/orders")
    def ui_orders(limit: int = 200):
        return clean(list(w.orders.find().sort("created_at", DESCENDING).limit(limit)))

    @app.get("/api/pallets")
    def ui_pallets(limit: int = 500):
        out = clean(list(w.pallets.find().sort("created_at", DESCENDING).limit(limit)))
        for p in out:
            p["ctn_total"] = len(p.pop("cartons", []))
            p["ctn_shipped"] = len(p.pop("shipped", []))
        return out

    @app.get("/api/locations")
    def ui_locations():
        return clean(list(w.locs.find().sort("code", ASCENDING).limit(5000)))

    @app.post("/api/locations/set")
    async def ui_loc_set(req: Request):
        b = await req.json()
        r = w.locs.update_one({"code": L.norm(b.get("code"))}, {"$set": {"state": b.get("state"),
                                                                       "pallet_id": b.get("pallet_id")}})
        return {"ok": r.matched_count == 1}

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
        b = await req.json()
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
    uvicorn.run(build_app(w), host=cfg["server"]["host"], port=cfg["server"]["port"], log_level="warning")


if __name__ == "__main__":
    main()
