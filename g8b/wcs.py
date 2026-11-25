"""WCS B - Warehouse Control System của luồng B.

Phía WMS: cùng đường dẫn / định dạng với G8-WCS gốc (move-service, identify-service, export-service).
Phía trạm: cùng device API với WCS gốc (G8-ID /ids/*, G8-Export /scs/*) - không sửa phần mềm trạm.
Phía RCS: ICS HuaRay V3.0.6 - addTask, cancelTask (mảng), getTaskOrderStatus, callback taskStatusReport.
Chạy: python -m g8b.wcs
"""
from __future__ import annotations

import base64
import copy
import logging
import os
import re
import threading
import time
from datetime import timedelta
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from . import locations as L
from .common import (MsgLog, ROOT, ci_get, clean, deep_merge, hourly, site_port, http_post, load_config, mongo_db, now,
                     save_config, setup_logging, today_start)
from .outbox import Outbox, accept_code

VERSION = "0.7.0"

DEFAULTS: dict = {
    "server": {"host": "0.0.0.0", "port": 9000},
    "mongo": {"uri": "mongodb://127.0.0.1:27018", "db": "g8b_wcs"},
    "wms": {"url": "http://127.0.0.1:9100", "user": "WCS_API_User", "password": "123456", "app_name": "TMS",
            "paths": {"login": "/API/General/Sys/Login",
                      "confirm_move": "/API/G8/Customer/Update/Pallet_Location",
                      "finished_goods": "/API/G8/Customer/Update/Finished_Goods",
                      "ob_pallet": "/API/G8/Customer/Update/OB_Pallet",
                      "location_stocks": "/API/G8/Customer/Update/Location_Stocks"}},
    "rcs": {"url": "http://127.0.0.1:7100", "area_id": 2, "from_system": "WCS-B", "order_prefix": "VB-",
            "poll_s": 2.0, "timeout_s": 10, "running_alert_min": 30,
            "templates": {"1": "forkAutoTemplate2", "2": "liftMoveShelf2"},
            "high_priority_moves": [2, 14], "priority_high": 4, "priority_normal": 6,
            "path_by": "code"},          # taskPath: "code" = mã vị trí (tên điểm RCS) | "node" = Node trong file highbay
    "locations": {"file": "config/locations.xlsx", "warehouses": "config/warehouses.json", "check": True},
    "identify_lines": [],
    "export_lines": [],
    "outbox": {"retry_s": 5, "timeout_s": 10},
    "worker_s": 1.0,
    "messages_keep_days": 14,
}

TASK_RE = re.compile(r"^(\d+)/(\d+)_(.+)$")

# Trạng thái task di chuyển (move)
CREATED, DISPATCHED, RUNNING, SUCCEEDED, FAILED, CANCELLED, SKIPPED = (
    "CREATED", "DISPATCHED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "SKIPPED")
FINAL = (SUCCEEDED, FAILED, CANCELLED, SKIPPED)
ACTIVE = (DISPATCHED, RUNNING)

log = logging.getLogger("wcs")


def ack_wms(ok: bool, desc: str = "success", **extra) -> dict:
    """ACK cho WMS giống WCS gốc: HTTP 200, code 200 / 201."""
    return {"code": 200 if ok else 201, "desc": desc if not ok or desc else "success", **extra}


def ack_dev(ok: bool = True, desc: str = "success") -> dict:
    """ACK cho thiết bị (RCS / IDS / SCS): code 1000 / 1001."""
    return {"code": 1000 if ok else 1001, "desc": desc}


class WCS:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.db = mongo_db(cfg["mongo"]["uri"], cfg["mongo"]["db"])
        self.msg = MsgLog(self.db, cfg["messages_keep_days"])
        o = cfg["outbox"]
        self.outbox = Outbox(self.db, self.msg, o["retry_s"], 0, o["timeout_s"])
        self.moves = self.db["moves"]
        self.id_orders = self.db["identify_orders"]
        self.id_pallets = self.db["identify_pallets"]
        self.do_orders = self.db["export_orders"]
        self.lines = self.db["lines"]
        self.locs = self.db["locations"]
        self.alerts = self.db["alerts"]
        self.counters = self.db["counters"]
        self._token: str | None = None
        self._token_lock = threading.Lock()
        self._stop = threading.Event()
        self._move_lock = threading.Lock()
        self._do_lock = threading.Lock()
        self.rcs_ok: bool | None = None
        self.started_at = now()
        self._indexes()
        self._load_lines()
        self.locstore = L.LocationStore(self.db, self.cfg, self._station_points)
        self.locstore.load()
        self._register_outbox()

    # ------------------------------------------------------------------ khởi tạo
    def _indexes(self):
        self.moves.create_index("task_id", unique=True)
        self.moves.create_index("order_id", unique=True, name="order_id_str",
                                partialFilterExpression={"order_id": {"$type": "string"}})
        self.moves.create_index([("group_key", ASCENDING), ("n", ASCENDING)])
        self.moves.create_index([("status", ASCENDING)])
        self.moves.create_index("final_at", expireAfterSeconds=30 * 86400)
        self.id_orders.create_index("key", unique=True)
        self.id_orders.create_index("final_at", expireAfterSeconds=30 * 86400)
        self.id_pallets.create_index("task_id", unique=True)
        self.id_pallets.create_index([("order_key", ASCENDING), ("pallet_id", ASCENDING)])
        self.id_pallets.create_index("final_at", expireAfterSeconds=30 * 86400)
        self.do_orders.create_index("do_id", unique=True)
        self.do_orders.create_index("final_at", expireAfterSeconds=30 * 86400)
        self.lines.create_index("line_id", unique=True)
        self.alerts.create_index("t", expireAfterSeconds=14 * 86400)

    def _load_lines(self):
        for kind, key in (("IDENTIFY", "identify_lines"), ("EXPORT", "export_lines")):
            for ln in self.cfg[key]:
                self.lines.update_one(
                    {"line_id": ln["line_id"]},
                    {"$set": {"kind": kind, "url": ln["url"].rstrip("/"), "name": ln.get("name", ""),
                              "location": L.norm(ln.get("location", ""))},
                     "$setOnInsert": {"enabled": bool(ln.get("enabled", True)), "busy_do": None,
                                      "last_seen": None}},
                    upsert=True)

    def _station_points(self) -> list[str]:
        return [ln.get("location", "") for ln in self.cfg["identify_lines"] + self.cfg["export_lines"]]

    def _register_outbox(self):
        ob = self.outbox
        ob.register("wms", self._wms_accept, build=self._wms_build, on_done=self._wms_done,
                    on_dead=self._wms_dead)
        ob.register("ids", accept_code(1000), on_dead=self._dev_dead)
        ob.register("scs", accept_code(1000), on_dead=self._dev_dead)

    def alert(self, level: str, text: str, **ref):
        log.warning("[ALERT %s] %s %s", level, text, ref or "")
        self.alerts.insert_one({"t": now(), "level": level, "text": text, "ref": ref})

    def next_seq(self, name: str) -> int:
        d = self.counters.find_one_and_update({"_id": name}, {"$inc": {"v": 1}}, upsert=True,
                                              return_document=ReturnDocument.AFTER)
        return int(d["v"])

    # ------------------------------------------------------------------ WMS client (token TKELog)
    def _login(self) -> str:
        w = self.cfg["wms"]
        body = {"Auth": {"Token": "", "App_Name": w["app_name"]},
                "Data": {"User_Name": w["user"], "Password": w["password"]}}
        status, resp, err = http_post(w["url"] + w["paths"]["login"], body, 10, self.msg, "WMS")
        data = ci_get(resp or {}, "data", default={}) or {}
        tok = ci_get(data, "token_ID", "Token") if isinstance(data, dict) else None
        if not tok:
            raise RuntimeError(f"Login WMS lỗi: {err or resp}")
        return tok

    def token(self, refresh: bool = False) -> str:
        with self._token_lock:
            if refresh or not self._token:
                self._token = self._login()
            return self._token

    def wms_location_free(self, t: dict) -> bool | None:
        """Hỏi WMS ô đích có hàng không (Location_Stocks, gọi ngay). True = trống, False = có hàng, None = không hỏi được."""
        w = self.cfg["wms"]
        data = {"Task_ID": t["task_id"], "Location": t["loc_new"], "Pallet_ID": t.get("pallet_id", "")}
        for refresh in (False, True):
            try:
                body = {"Auth": {"Token": self.token(refresh), "App_Name": w["app_name"]}, "Data": data}
            except RuntimeError as e:
                log.warning("Location_Stocks: %s", e)
                return None
            status, resp, err = http_post(w["url"] + w["paths"]["location_stocks"], body, 10, self.msg, "WMS")
            msg = ci_get(resp or {}, "message", default={}) if isinstance(resp, dict) else {}
            code = ci_get(msg or {}, "message_Code", default=None)
            if code == 203 and not refresh:
                continue
            if status == 200 and code == 200:
                st = ci_get(ci_get(resp, "data", default={}) or {}, "Status", default=None)
                return None if st is None else not bool(st)
            return None
        return None

    def _wms_build(self, job):
        w = self.cfg["wms"]
        return (w["url"] + w["paths"][job["ref"]["api"]],
                {"Auth": {"Token": self.token(), "App_Name": w["app_name"]}, "Data": job["body"]}, {})

    def _wms_accept(self, status: int, resp: Any):
        msg = ci_get(resp or {}, "message", default={}) if isinstance(resp, dict) else {}
        code = ci_get(msg or {}, "message_Code", default=None)
        desc = ci_get(msg or {}, "message_Desc", default="")
        try:
            code = int(code)
        except (TypeError, ValueError):
            pass
        if status == 200 and code == 200:
            return True, ""
        if code == 203:  # token hết hạn
            with self._token_lock:
                self._token = None
            return False, "token hết hạn (203)"
        if status == 200 and isinstance(code, int):
            return False, f"WMS từ chối: {code} {desc}", True
        return False, f"HTTP {status} {resp}"

    @staticmethod
    def wms_data(resp) -> dict:
        d = ci_get(resp or {}, "data", default={}) if isinstance(resp, dict) else {}
        return d if isinstance(d, dict) else {}

    def send_wms(self, api: str, key: str, data: dict, ref: dict):
        self.outbox.put("wms", key, "", data, {"api": api, **ref}, "WMS")

    def _wms_done(self, job, resp):
        ref = job["ref"]
        if ref.get("move"):
            self.moves.update_one({"task_id": ref["move"]}, {"$set": {"reported": True, "reported_at": now()}})
        elif ref.get("id_task"):
            self._identify_reported(ref["id_task"], True, self.wms_data(resp), "")
        elif ref.get("do"):
            self._export_reported(ref["do"], ref["pallet"])

    def _wms_dead(self, job, resp, why):
        ref = job["ref"]
        self.alert("ERROR", f"WMS từ chối bản tin {ref.get('api')}: {why}", key=job["key"])
        if ref.get("id_task"):
            self._identify_reported(ref["id_task"], False, {}, why)
        elif ref.get("move"):
            self.moves.update_one({"task_id": ref["move"]}, {"$set": {"reported": "REJECTED"}})

    def _dev_dead(self, job, resp, why):
        self.alert("ERROR", f"Trạm từ chối lệnh: {why}", key=job["key"], url=job["url"])
        ref = job.get("ref", {})
        if ref.get("do"):
            self._finish_do(ref["do"], "FAILED", why)
        if ref.get("id_task"):
            self.id_pallets.update_one({"task_id": ref["id_task"]},
                                       {"$set": {"status": "FAILED", "error": why, "final_at": now()}})

    # ------------------------------------------------------------------ vị trí
    def loc_get(self, code: str) -> dict | None:
        return self.locs.find_one({"code": L.norm(code)})

    def loc_check(self, code: str) -> str:
        if not self.cfg["locations"]["check"]:
            return ""
        d = self.loc_get(code)
        if not d:
            return f"Vị trí {code} không có trong danh sách của WCS B"
        if d["state"] == L.LOCKED:
            return f"Vị trí {code} đang bị khoá"
        return ""

    # ================================================================== MOVE
    def move_create(self, p: dict, manual: bool = False) -> dict:
        need = ["Task_ID", "WH_Code", "Pallet_ID", "Type_Robot_ID", "Type_Move_ID", "Location_Old", "Location_New"]
        miss = [k for k in need if ci_get(p, k) in (None, "")]
        if miss:
            return ack_wms(False, "Missing " + ", ".join(miss))
        task_id = str(ci_get(p, "Task_ID")).strip()
        m = TASK_RE.match(task_id)
        n, total, gkey = (int(m.group(1)), int(m.group(2)), m.group(3)) if m else (1, 1, task_id)
        if n < 1 or n > total:
            return ack_wms(False, f"Task_ID {task_id} sai thứ tự n/m")
        payload = {k: ci_get(p, k) for k in p}  # giữ nguyên payload gốc để báo lại WMS
        try:
            tm = int(ci_get(p, "Type_Move_ID"))
        except (TypeError, ValueError):
            tm = 0
        doc = {"task_id": task_id, "group_key": gkey, "n": n, "m": total, "payload": payload,
               "pallet_id": str(ci_get(p, "Pallet_ID")), "loc_old": L.norm(ci_get(p, "Location_Old")),
               "loc_new": L.norm(ci_get(p, "Location_New")), "type_robot": ci_get(p, "Type_Robot_ID"),
               "type_move": tm, "status": CREATED, "attempt": 0, "reported": False, "manual": manual,
               "created_at": now(), "updated_at": now(), "history": [{"t": now(), "s": CREATED}]}
        try:
            self.moves.insert_one(doc)
        except DuplicateKeyError:
            old = self.moves.find_one({"task_id": task_id})
            if old and old["payload"] == payload:
                return ack_wms(True, "success (duplicate ignored)")
            return ack_wms(False, f"Duplicate Task_ID {task_id}")
        return ack_wms(True)

    def _set_move(self, t: dict, status: str, **fields):
        upd = {"status": status, "updated_at": now(), **fields}
        if status in FINAL:
            upd["final_at"] = now()
        self.moves.update_one({"_id": t["_id"]}, {"$set": upd,
                                                  "$push": {"history": {"t": now(), "s": status,
                                                                        "note": fields.get("error", "")}}})
        log.info("move %s -> %s %s", t["task_id"], status, fields.get("error", ""))
        if status in FINAL:
            self._move_final(self.moves.find_one({"_id": t["_id"]}))

    def _move_final(self, t: dict):
        # giải phóng / cập nhật ô
        if t["status"] == SUCCEEDED:
            self.locs.update_one({"code": t["loc_old"], "state": {"$ne": L.LOCKED}},
                                 {"$set": {"state": L.EMPTY, "pallet_id": None, "task": None}})
            self.locs.update_one({"code": t["loc_new"]},
                                 {"$set": {"state": L.FULL, "pallet_id": t["pallet_id"], "task": None}})
        else:
            self.locs.update_one({"code": t["loc_new"], "task": t["task_id"]},
                                 {"$set": {"state": L.EMPTY, "task": None}})
        if t.get("manual"):
            self.moves.update_one({"_id": t["_id"]}, {"$set": {"reported": "MANUAL"}})
            return  # lệnh đặt tay trên WCS B: không báo WMS
        # báo WMS - giữ nguyên payload gốc + status như WCS gốc
        data = dict(t["payload"])
        data["status"] = "success" if t["status"] == SUCCEEDED else "false"
        self.send_wms("confirm_move", f"move:{t['task_id']}:{t['attempt']}", data, {"move": t["task_id"]})

    def _rcs(self, path: str, body: Any) -> tuple[int, Any, str]:
        r = self.cfg["rcs"]
        res = http_post(r["url"] + path, body, r["timeout_s"], self.msg, "RCS")
        self.rcs_ok = res[0] == 200
        return res

    def _dispatch(self, t: dict):
        r = self.cfg["rcs"]
        for code in (t["loc_old"], t["loc_new"]):
            err = self.loc_check(code)
            if err:
                return self._set_move(t, FAILED, error=err, reason="LocationFailed")
        tpl = r["templates"].get(str(t["type_robot"]))
        if not tpl:
            return self._set_move(t, FAILED, error=f"Type_Robot_ID {t['type_robot']} không hỗ trợ",
                                  reason="ModelCodeFailed")
        if self.cfg["locations"]["check"]:
            # giữ chỗ ô đích (nguyên tử): chỉ khi ô không bận / không khoá
            # ô kệ phải trống; điểm trạm (bàn định danh, đầu băng tải) chỉ cần không bận
            dest = self.locs.find_one_and_update(
                {"code": t["loc_new"], "$or": [{"task": None}, {"task": t["task_id"]}],
                 "$and": [{"$or": [{"state": L.EMPTY}, {"kind": {"$ne": "RACK"}, "state": L.FULL}]}]},
                {"$set": {"state": L.BUSY, "task": t["task_id"]}})
            if dest is None:
                d = self.loc_get(t["loc_new"])
                if d and d.get("task") and d["task"] != t["task_id"]:
                    return  # ô đích đang được task khác dùng - chờ
                if d and d.get("kind") == "RACK" and d["state"] == L.FULL and self.wms_location_free(t) is True:
                    # sổ WCS ghi "có hàng" nhưng WMS (nguồn tồn kho) báo trống - vd. pallet được dời tay / kiểm kê
                    self.locs.update_one({"code": d["code"], "state": L.FULL, "task": None},
                                         {"$set": {"state": L.EMPTY, "pallet_id": None}})
                    self.alert("INFO", f"Ô {d['code']}: WMS báo trống (Location_Stocks) - đồng bộ lại trạng thái ô",
                               task=t["task_id"])
                    return self._dispatch(t)
                return self._set_move(t, FAILED, error=f"Ô đích {t['loc_new']} không sẵn sàng ({d and d['state']})",
                                      reason="LocationBusy")
        order_id = t.get("order_id") or f"{r['order_prefix']}{self.next_seq('rcs_order'):07d}"
        if not t.get("order_id"):
            self.moves.update_one({"_id": t["_id"]}, {"$set": {"order_id": order_id}})
        prio = r["priority_high"] if t["type_move"] in r["high_priority_moves"] else r["priority_normal"]
        body = {"modelProcessCode": tpl, "priority": prio, "fromSystem": r["from_system"], "orderId": order_id,
                "taskOrderDetail": [{"taskPath": f"{self.rcs_point(t['loc_old'])},{self.rcs_point(t['loc_new'])}",
                                     "shelfNumber": ""}]}
        status, resp, err = self._rcs("/ics/taskOrder/addTask", body)
        code = resp.get("code") if isinstance(resp, dict) else None
        if code in (1000, 2111):  # 2111 = đã có orderId này trên RCS (gửi lại sau khi khởi động lại)
            self._set_move(t, DISPATCHED, order_id=order_id, dispatched_at=now(), error="")
        elif err or status != 200:
            tries = t.get("dispatch_tries", 0) + 1
            self.moves.update_one({"_id": t["_id"]}, {"$set": {"dispatch_tries": tries, "error": err or f"HTTP {status}",
                                                              "next_try": now() + timedelta(seconds=5)}})
            if tries == 3:
                self.alert("WARN", f"Không gửi được task {t['task_id']} sang RCS: {err or status}")
        else:
            self._set_move(t, FAILED, order_id=order_id, error=f"RCS từ chối: {code} {resp.get('desc')}",
                           reason="RcsRejected")

    def rcs_point(self, code: str) -> str:
        """Điểm gửi RCS: mã vị trí (tên điểm) hoặc Node trong file highbay, theo rcs.path_by."""
        if self.cfg["rcs"].get("path_by") == "node":
            d = self.loc_get(code)
            if d and d.get("node"):
                return d["node"]
        return code

    def move_worker_tick(self):
        with self._move_lock:
            cand = list(self.moves.find({"status": CREATED}).sort([("created_at", ASCENDING)]).limit(200))
            ready = []
            for t in cand:
                if t.get("next_try") and t["next_try"] > now():
                    continue
                grp = list(self.moves.find({"group_key": t["group_key"]}, {"n": 1, "m": 1, "status": 1}))
                if len({g["n"] for g in grp}) < t["m"]:
                    continue  # chưa nhận đủ task trong nhóm
                prev = [g for g in grp if g["n"] < t["n"]]
                if any(g["status"] in (FAILED, CANCELLED, SKIPPED) for g in prev):
                    self._set_move(t, SKIPPED, error="Task trước trong nhóm không thành công")
                    continue
                if all(g["status"] == SUCCEEDED for g in prev):
                    ready.append(t)
            r = self.cfg["rcs"]
            ready.sort(key=lambda x: (0 if x["type_move"] in r["high_priority_moves"] else 1, x["created_at"]))
            for t in ready:
                if self.moves.count_documents({"pallet_id": t["pallet_id"], "status": {"$in": list(ACTIVE)}}):
                    continue  # pallet đang được robot chở
                self._dispatch(t)

    # --- trạng thái từ RCS (callback + hỏi định kỳ)
    def rcs_status(self, order_id: str, status: int, info: dict, source: str):
        t = self.moves.find_one({"order_id": order_id})
        if not t:
            return False
        if t["status"] in FINAL:
            return True
        pos = info.get("shelfCurrPosition") or info.get("qrContent") or ""
        extra = {"rcs_status": status, "rcs_pos": pos, "device": info.get("deviceNum") or t.get("device", ""),
                 "last_rcs_at": now()}
        if status == 8:
            self._set_move(t, SUCCEEDED, **extra)
        elif status == 7:
            self._set_move(t, FAILED, error=f"RCS báo thất bại: {info.get('errorDesc', '')}", reason="RcsFailed",
                           **extra)
        elif status == 3:
            self._set_move(t, CANCELLED, error="RCS báo đã hủy", **extra)
        elif status in (6, 9, 10, 20, 21, 22, 23):
            if t["status"] == DISPATCHED and status != 9:
                self._set_move(t, RUNNING, **extra)
            else:
                self.moves.update_one({"_id": t["_id"]}, {"$set": extra})
        else:
            self.moves.update_one({"_id": t["_id"]}, {"$set": extra})
        log.info("RCS %s %s status=%s pos=%s", source, order_id, status, pos)
        return True

    def poll_tick(self):
        r = self.cfg["rcs"]
        for t in self.moves.find({"status": {"$in": list(ACTIVE)}}):
            status, resp, err = self._rcs("/ics/out/task/getTaskOrderStatus", {"orderId": t["order_id"]})
            if err or not isinstance(resp, dict) or resp.get("code") != 1000:
                continue
            data = resp.get("data") or {}
            try:
                st = int(data.get("status"))
            except (TypeError, ValueError):
                continue
            det = (data.get("taskOrderDetail") or [{}])[-1] if data.get("taskOrderDetail") else {}
            self.rcs_status(t["order_id"], st, det if isinstance(det, dict) else {}, "poll")
            since = t.get("dispatched_at") or t["created_at"]
            if now() - since > timedelta(minutes=r["running_alert_min"]) and not t.get("long_alert"):
                self.moves.update_one({"_id": t["_id"]}, {"$set": {"long_alert": True}})
                self.alert("WARN", f"Task {t['task_id']} chạy quá {r['running_alert_min']} phút")

    # --- thao tác tay
    def move_cancel(self, task_id: str) -> dict:
        t = self.moves.find_one({"task_id": task_id})
        if not t:
            return {"ok": False, "msg": "không có task"}
        if t["status"] == CREATED:
            self._set_move(t, CANCELLED, error="Hủy tay")
            return {"ok": True}
        if t["status"] in ACTIVE:
            status, resp, err = self._rcs("/ics/out/task/cancelTask", [{"orderId": t["order_id"]}])
            ok = isinstance(resp, dict) and resp.get("code") == 1000
            return {"ok": ok, "msg": "Đã gửi hủy sang RCS, chờ RCS báo" if ok else (err or resp)}
        return {"ok": False, "msg": f"task đang {t['status']}"}

    def move_complete(self, task_id: str) -> dict:
        t = self.moves.find_one({"task_id": task_id})
        if not t or t["status"] in FINAL:
            return {"ok": False, "msg": "task không ở trạng thái đang chạy / chờ"}
        self._set_move(t, SUCCEEDED, error="Hoàn thành tay")
        return {"ok": True}

    def move_retry(self, task_id: str) -> dict:
        """Chạy lại task lỗi và các task sau nó trong nhóm (orderId RCS mới, báo WMS lại theo Task_ID gốc)."""
        t = self.moves.find_one({"task_id": task_id})
        if not t or t["status"] not in (FAILED, CANCELLED, SKIPPED):
            return {"ok": False, "msg": "chỉ chạy lại task FAILED / CANCELLED / SKIPPED"}
        n = 0
        for g in self.moves.find({"group_key": t["group_key"], "n": {"$gte": t["n"]},
                                  "status": {"$in": [FAILED, CANCELLED, SKIPPED]}}):
            self.moves.update_one({"_id": g["_id"]}, {
                "$set": {"status": CREATED, "error": "", "reported": False,
                         "dispatch_tries": 0, "updated_at": now()},
                "$unset": {"final_at": "", "order_id": "", "reason": ""}, "$inc": {"attempt": 1},
                "$push": {"history": {"t": now(), "s": CREATED, "note": "chạy lại"}}})
            n += 1
        return {"ok": True, "count": n}

    # ================================================================== IDENTIFY
    def identify_create(self, p: dict, kind: str) -> dict:
        po_field = "PO_ID" if kind == "Product" else "ASN_ID"
        po_id = ci_get(p, po_field)
        line_id = str(ci_get(p, "Line_ID", default="") or "")
        items = ci_get(p, "Item_Information", "info_detail", default=None)
        if po_id in (None, "", 0):
            return ack_wms(False, f"Missing {po_field}")
        if not isinstance(items, list) or not items:
            return ack_wms(False, "Missing Item_Information")
        line = self.lines.find_one({"line_id": line_id, "kind": "IDENTIFY"})
        if not line:
            return ack_wms(False, f"Line_ID {line_id} không thuộc WCS B")
        if not line.get("enabled"):
            return ack_wms(False, f"Line {line_id} đang tắt trên WCS B (bảo trì / cutover)")
        for i, it in enumerate(items):
            if not ci_get(it, "Pallet_ID") or not ci_get(it, "Carton_List"):
                return ack_wms(False, f"Item {i + 1}: thiếu Pallet_ID hoặc Carton_List")
        key = f"{kind}:{po_id}"
        wh = ci_get(p, "WH_Code", default="")
        self.id_orders.update_one({"key": key}, {
            "$setOnInsert": {"key": key, "kind": kind, "po_id": po_id, "po_name": ci_get(p, "PO_Name", default=""),
                             "wh": wh, "line_id": line_id, "status": "RUNNING", "created_at": now(),
                             "pallets_total": 0, "pallets_done": 0}}, upsert=True)
        base = int(ci_get(p, "Pallet_Current", default=0) or 0)
        added = 0
        for i, it in enumerate(items):
            pallet = str(ci_get(it, "Pallet_ID"))
            if self.id_pallets.find_one({"order_key": key, "pallet_id": pallet,
                                         "status": {"$nin": ["CANCELLED", "FAILED"]}}):
                continue  # gửi lặp
            seq = base + i if base else i + 1
            task_id = f"{seq}-{po_id}__{now().strftime('%H:%M:%S.%f')}"
            body = {"Task_ID": task_id, "PO_ID": po_id, "PO_Name": ci_get(p, "PO_Name", default=""),
                    "WH_Code": wh, "Line_ID": line_id, "info_detail": [it], "Pallet_Current": seq,
                    "fromSystem": "IdentifyService"}
            self.id_pallets.insert_one({"task_id": task_id, "order_key": key, "kind": kind, "po_id": po_id,
                                        "line_id": line_id, "pallet_id": pallet, "wh": wh,
                                        "location": L.norm(ci_get(it, "Location", default="")),
                                        "ctn_plan": len(ci_get(it, "Carton_List") or []),
                                        "status": "SENT", "created_at": now()})
            self.outbox.put("ids", f"ids-order:{task_id}", f"{line['url']}/orderTask", body,
                            {"id_task": task_id}, line_id)
            added += 1
        self.id_orders.update_one({"key": key}, {"$inc": {"pallets_total": added},
                                                 "$set": {"status": "RUNNING"}, "$unset": {"final_at": ""}})
        return ack_wms(True)

    def identify_cancel(self, p: dict) -> dict:
        po_id = ci_get(p, "PO_ID", "ASN_ID")
        if po_id in (None, ""):
            return ack_wms(False, "Missing PO_ID")
        orders = list(self.id_orders.find({"po_id": po_id, "status": "RUNNING"}))
        if not orders:
            return ack_wms(True, "success (không có lệnh đang chạy)")
        for o in orders:
            line = self.lines.find_one({"line_id": o["line_id"]})
            first = self.id_pallets.find_one({"order_key": o["key"]})
            body = {"Task_ID": first["task_id"] if first else "", "PO_ID": po_id, "Line_ID": o["line_id"],
                    "fromSystem": "IdentifyService"}
            if line:
                self.outbox.put("ids", f"ids-cancel:{o['key']}:{self.next_seq('cancel')}",
                                f"{line['url']}/cancelTask", body, {}, o["line_id"])
            self.id_pallets.update_many({"order_key": o["key"], "status": "SENT"},
                                        {"$set": {"status": "CANCELLED", "final_at": now()}})
            self.id_orders.update_one({"_id": o["_id"]}, {"$set": {"status": "CANCEL_REQUESTED"}})
            self._identify_check_done(o["key"])
        return ack_wms(True)

    def pallet_done(self, p: dict, peer: str) -> dict:
        task_id = ci_get(p, "Task_ID", default="")
        pallet = str(ci_get(p, "palletID", "Pallet_ID", default=""))
        q = {"task_id": task_id} if task_id else {"po_id": ci_get(p, "PO_ID"), "pallet_id": pallet}
        t = self.id_pallets.find_one(q) or self.id_pallets.find_one(
            {"po_id": ci_get(p, "PO_ID"), "pallet_id": pallet})
        self.lines.update_one({"line_id": ci_get(p, "Line_ID", default="")}, {"$set": {"last_seen": now()}})
        if not t:
            self.alert("WARN", f"pallet_identify_done không khớp lệnh nào của WCS B: {pallet}", peer=peer, body=p)
            return ack_dev(True)  # ACK để trạm không gửi lại mãi; đã cảnh báo
        if t["status"] in ("IDENTIFIED", "REPORTED"):
            return ack_dev(True)  # gửi lặp
        ctn = int(ci_get(p, "Ctn", default=0) or 0)
        self.id_pallets.update_one({"_id": t["_id"]}, {"$set": {"status": "IDENTIFIED", "ctn": ctn,
                                                                "identified_at": now()}})
        po_field = "PO_ID" if t["kind"] == "Product" else "ASN_ID"
        data = {po_field: t["po_id"], "WH_Code": t["wh"], "Line_ID": t["line_id"], "Pallet_ID": t["pallet_id"],
                "Ctn": ctn}
        self.send_wms("finished_goods", f"fg:{t['task_id']}", data, {"id_task": t["task_id"]})
        return ack_dev(True)

    def _identify_reported(self, task_id: str, ok: bool, data: dict, why: str):
        t = self.id_pallets.find_one({"task_id": task_id})
        if not t:
            return
        loc = L.norm(ci_get(data, "Location", default="") or t.get("location", ""))
        self.id_pallets.update_one({"_id": t["_id"]}, {"$set": {
            "status": "REPORTED" if ok else "WMS_REJECTED", "wms_location": loc, "final_at": now(),
            "error": why}})
        line = self.lines.find_one({"line_id": t["line_id"]})
        if line:
            po_field = "PO_ID" if t["kind"] == "Product" else "ASN_ID"
            body = {po_field: t["po_id"], "PO_ID": t["po_id"], "Line_ID": t["line_id"], "Pallet_ID": t["pallet_id"],
                    "Location": loc, "Status": "true" if ok else "false", "Message": why,
                    "fromSystem": "IdentifyService"}
            self.outbox.put("ids", f"ids-confirm:{task_id}", f"{line['url']}/confirm_product", body, {},
                            t["line_id"])
        self._identify_check_done(t["order_key"])

    def _identify_check_done(self, key: str):
        o = self.id_orders.find_one({"key": key})
        if not o:
            return
        left = self.id_pallets.count_documents({"order_key": key, "status": {"$in": ["SENT", "IDENTIFIED"]}})
        done = self.id_pallets.count_documents({"order_key": key, "status": "REPORTED"})
        upd: dict = {"pallets_done": done}
        if left == 0 and o["status"] in ("RUNNING", "CANCEL_REQUESTED"):
            upd["status"] = "COMPLETED" if o["status"] == "RUNNING" else "CANCELLED"
            upd["final_at"] = now()
        self.id_orders.update_one({"_id": o["_id"]}, {"$set": upd})

    # ================================================================== EXPORT
    def export_create(self, p: dict) -> dict:
        do_id = ci_get(p, "DO_ID")
        belt = str(ci_get(p, "Conveyor_Belt", default="") or "")
        items = ci_get(p, "Item_Information", default=None)
        if do_id in (None, "", 0) or not isinstance(items, list) or not items:
            return ack_wms(False, "Missing DO_ID / Item_Information")
        line = self.lines.find_one({"line_id": belt, "kind": "EXPORT"})
        if not line:
            return ack_wms(False, f"Conveyor_Belt {belt} không thuộc WCS B")
        if not line.get("enabled"):
            return ack_wms(False, f"Line {belt} đang tắt trên WCS B")
        payload = {k: ci_get(p, k) for k in p}
        doc = {"do_id": do_id, "line_id": belt, "wh": ci_get(p, "WH_Code", default=""), "payload": payload,
               "pallets": [str(ci_get(i, "Pallet_ID")) for i in items], "done_pallets": [],
               "status": "QUEUED", "created_at": now()}
        try:
            self.do_orders.insert_one(doc)
        except DuplicateKeyError:
            old = self.do_orders.find_one({"do_id": do_id})
            if old and old["payload"] == payload:
                return ack_wms(True, "success (duplicate ignored)")
            return ack_wms(False, f"Duplicate DO_ID {do_id}")
        return ack_wms(True)

    def export_worker_tick(self):
        with self._do_lock:
            for d in self.do_orders.find({"status": "QUEUED"}).sort("created_at", ASCENDING):
                line = self.lines.find_one({"line_id": d["line_id"]})
                if not line or not line.get("enabled") or line.get("busy_do") not in (None, d["do_id"]):
                    continue
                self.lines.update_one({"_id": line["_id"]}, {"$set": {"busy_do": d["do_id"]}})
                body = dict(d["payload"])
                body["fromSystem"] = "ExportService"
                self.outbox.put("scs", f"scs-order:{d['do_id']}", f"{line['url']}/orderTask", body,
                                {"do": d["do_id"]}, d["line_id"])
                self.do_orders.update_one({"_id": d["_id"]}, {"$set": {"status": "RUNNING", "sent_at": now()}})

    def scs_report(self, p: dict, peer: str) -> dict:
        do_id = ci_get(p, "DO_ID")
        pallet = str(ci_get(p, "Pallet_ID", default=""))
        d = self.do_orders.find_one({"do_id": do_id})
        self.lines.update_one({"line_id": ci_get(p, "Conveyor_Belt", default="")}, {"$set": {"last_seen": now()}})
        if not d:
            self.alert("WARN", f"taskStatusReport DO {do_id} không thuộc WCS B", peer=peer)
            return ack_dev(True)
        if pallet in d["done_pallets"] or pallet in d.get("sent_pallets", []):
            return ack_dev(True)
        cartons = ci_get(p, "Carton_List", default=[]) or []
        data = {"DO_ID": do_id, "WH_Code": d["wh"], "Conveyor_Belt": d["line_id"],
                "Item_Information": [{"Pallet_ID": pallet, "Item_Code": ci_get(p, "Item_Code", default=""),
                                      "Ctn": len(cartons) or int(ci_get(p, "Ctn", default=0) or 0),
                                      "Carton_List": cartons}]}
        self.do_orders.update_one({"_id": d["_id"]}, {"$addToSet": {"sent_pallets": pallet}})
        self.send_wms("ob_pallet", f"ob:{do_id}:{pallet}", data, {"do": do_id, "pallet": pallet})
        return ack_dev(True)

    def _export_reported(self, do_id, pallet):
        d = self.do_orders.find_one_and_update({"do_id": do_id}, {"$addToSet": {"done_pallets": pallet}},
                                               return_document=ReturnDocument.AFTER)
        if d and set(d["pallets"]) <= set(d["done_pallets"]):
            self._finish_do(do_id, "COMPLETED", "")

    def _finish_do(self, do_id, status, why):
        d = self.do_orders.find_one_and_update({"do_id": do_id, "status": {"$in": ["QUEUED", "RUNNING"]}},
                                               {"$set": {"status": status, "final_at": now(), "error": why}})
        if d:
            self.lines.update_one({"line_id": d["line_id"], "busy_do": do_id}, {"$set": {"busy_do": None}})

    def export_close(self, do_id) -> dict:
        """Đóng tay DO (xuất thiếu / hủy) để giải phóng line."""
        self._finish_do(do_id, "CLOSED", "Đóng tay")
        return {"ok": True}

    # ================================================================== đặt lệnh tay / cấu hình / dashboard
    def manual_move(self, b: dict) -> dict:
        """Move đặt tay trên WCS B (không đến từ WMS, không báo WMS)."""
        steps = b.get("steps") or [{"from": b.get("from"), "to": b.get("to")}]
        steps = [s for s in steps if s.get("from") and s.get("to")]
        if not steps or not b.get("pallet_id"):
            return {"ok": False, "msg": "Cần pallet và ít nhất 1 bước từ → đến"}
        key = f"MANUAL_{now().strftime('%Y-%m-%d__%H:%M:%S.%f')}"
        ids = []
        for i, st in enumerate(steps, 1):
            tid = f"{i}/{len(steps)}_{key}"
            r = self.move_create({"Task_ID": tid, "WH_Code": "MANUAL", "Pallet_ID": b["pallet_id"],
                                  "Type_Robot_ID": int(b.get("type_robot", 1)),
                                  "Type_Move_ID": int(b.get("type_move", 11)),
                                  "Location_Old": st["from"], "Location_New": st["to"]}, manual=True)
            if r["code"] != 200:
                return {"ok": False, "msg": r["desc"]}
            ids.append(tid)
        log.info("Đặt lệnh Move tay %s", ids)
        return {"ok": True, "task_ids": ids}

    RAW = {"move": "move_pallet", "identify": "identify-cartons", "import": "identify-import",
           "cancel": "identify-cartons/cancel", "export": "export_items"}

    def raw_order(self, kind: str, body: dict) -> dict:
        """Bản tin WMS dán tay (test): chạy đúng như WMS gọi vào."""
        fn = {"move": self.move_create, "identify": lambda x: self.identify_create(x, "Product"),
              "import": lambda x: self.identify_create(x, "Import"), "cancel": self.identify_cancel,
              "export": self.export_create}.get(kind)
        if not fn or not isinstance(body, dict):
            return {"ok": False, "msg": "loại / body không hợp lệ"}
        self.msg.add("IN", "UI (tay)", f"/{self.RAW[kind]}", body)
        r = fn(body)
        return {"ok": r.get("code") == 200, "msg": r.get("desc"), "resp": r}

    EDITABLE = ("wms", "rcs", "locations", "identify_lines", "export_lines", "outbox", "worker_s",
                "messages_keep_days")

    def config_view(self) -> dict:
        c = {k: copy.deepcopy(self.cfg[k]) for k in self.EDITABLE}
        # trạng thái bật/tắt thật của line nằm trong DB (đổi ở tab Trạm / Line)
        en = {x["line_id"]: x.get("enabled", True) for x in self.lines.find({}, {"line_id": 1, "enabled": 1})}
        for key in ("identify_lines", "export_lines"):
            for ln in c[key]:
                ln["enabled"] = en.get(ln["line_id"], ln.get("enabled", True))
        return {"config": c, "server": self.cfg["server"], "mongo": self.cfg["mongo"], "file": self.cfg["_path"]}

    def config_save(self, new: dict) -> dict:
        errs = []
        lines_all = []
        for key in ("identify_lines", "export_lines"):
            for ln in new.get(key, []):
                ln["line_id"] = str(ln.get("line_id", "")).strip()
                ln["url"] = str(ln.get("url", "")).strip().rstrip("/")
                ln["location"] = L.norm(ln.get("location", ""))
                if not ln["line_id"] or not ln["url"].startswith("http"):
                    errs.append(f"{key}: line cần line_id và url http://...")
                lines_all.append(ln["line_id"])
        if len(set(lines_all)) != len(lines_all):
            errs.append("Trùng line_id")
        for k in ("wms", "rcs"):
            if not str(new.get(k, {}).get("url", "")).startswith("http"):
                errs.append(f"{k}.url phải bắt đầu bằng http")
        removed = {x["line_id"] for x in self.lines.find()} - set(lines_all)
        for lid in removed:
            busy = self.lines.find_one({"line_id": lid, "busy_do": {"$ne": None}}) or \
                self.id_pallets.find_one({"line_id": lid, "status": {"$in": ["SENT", "IDENTIFIED"]}})
            if busy:
                errs.append(f"Không xoá được line {lid}: đang có lệnh chạy")
        if errs:
            return {"ok": False, "msg": "; ".join(errs)}
        merged = deep_merge(self.cfg, {k: new[k] for k in self.EDITABLE if k in new})
        for key in ("identify_lines", "export_lines"):
            merged[key] = new.get(key, [])
        self.cfg.clear()
        self.cfg.update(merged)
        save_config(self.cfg)
        self.outbox.retry_s = float(self.cfg["outbox"]["retry_s"])
        self.outbox.timeout_s = float(self.cfg["outbox"]["timeout_s"])
        with self._token_lock:
            self._token = None  # WMS có thể đổi tài khoản
        for lid in removed:
            self.lines.delete_one({"line_id": lid})
        self._load_lines()
        for key in ("identify_lines", "export_lines"):
            for ln in self.cfg[key]:
                self.lines.update_one({"line_id": ln["line_id"]},
                                      {"$set": {"enabled": bool(ln.get("enabled", True))}})
        self.locstore.load()  # file / zone / điểm sàn của line có thể đã đổi
        log.info("Lưu cấu hình từ giao diện -> %s", self.cfg["_path"])
        return {"ok": True, "msg": f"Đã lưu {self.cfg['_path']} và áp dụng"}

    def dashboard(self) -> dict:
        t0 = today_start()
        mv = self.moves
        ob = self.db["outbox"]
        last_wms = ob.find_one({"kind": "wms", "tries": {"$gt": 0}}, sort=[("next_at", DESCENDING)])
        wms_ok = None if not last_wms else (last_wms["status"] == "DONE" or not last_wms.get("last_error"))
        occ: dict = {}
        for r in self.locs.aggregate([{"$group": {"_id": {"z": "$zone", "s": "$state"}, "n": {"$sum": 1}}}]):
            occ.setdefault(r["_id"]["z"] or "-", {})[r["_id"]["s"]] = r["n"]
        lines = []
        for ln in self.lines.find().sort("line_id", ASCENDING):
            if ln["kind"] == "IDENTIFY":
                act = self.id_pallets.count_documents({"line_id": ln["line_id"],
                                                       "status": {"$in": ["SENT", "IDENTIFIED"]}})
            else:
                act = 1 if ln.get("busy_do") else 0
            lines.append({"line_id": ln["line_id"], "kind": ln["kind"], "name": ln.get("name", ""),
                          "enabled": ln.get("enabled", True), "active": act, "busy_do": ln.get("busy_do"),
                          "last_seen": ln.get("last_seen")})
        return clean({
            "now": now(), "rcs_ok": self.rcs_ok, "wms_ok": wms_ok,
            "tiles": {
                "move_running": mv.count_documents({"status": {"$in": list(ACTIVE)}}),
                "move_waiting": mv.count_documents({"status": CREATED}),
                "move_done_today": mv.count_documents({"status": SUCCEEDED, "final_at": {"$gte": t0}}),
                "move_fail_today": mv.count_documents({"status": {"$in": [FAILED, CANCELLED, SKIPPED]},
                                                       "final_at": {"$gte": t0}}),
                "pallet_identified_today": self.id_pallets.count_documents({"status": "REPORTED",
                                                                            "final_at": {"$gte": t0}}),
                "do_running": self.do_orders.count_documents({"status": {"$in": ["QUEUED", "RUNNING"]}}),
                "do_done_today": self.do_orders.count_documents({"status": "COMPLETED", "final_at": {"$gte": t0}}),
                "outbox_pending": ob.count_documents({"status": {"$in": ["PENDING", "SENDING"]}}),
                "outbox_dead": ob.count_documents({"status": "DEAD"}),
                "alerts_24h": self.alerts.count_documents({"t": {"$gte": now() - timedelta(hours=24)}}),
            },
            "hourly_moves": hourly(mv, "final_at", {"status": SUCCEEDED}),
            "hourly_pallets": hourly(self.id_pallets, "final_at", {"status": "REPORTED"}),
            "occupancy": occ, "lines": lines,
            "alerts": list(self.alerts.find().sort("t", DESCENDING).limit(6)),
            "recent_fail": list(mv.find({"status": {"$in": [FAILED, CANCELLED, SKIPPED]}},
                                        {"task_id": 1, "pallet_id": 1, "status": 1, "error": 1, "final_at": 1})
                                .sort("final_at", DESCENDING).limit(5)),
        })

    # ================================================================== nền
    def start(self):
        self.outbox.start()
        threading.Thread(target=self._loop, args=(self.move_worker_tick, self.cfg["worker_s"]), daemon=True,
                         name="move").start()
        threading.Thread(target=self._loop, args=(self.export_worker_tick, self.cfg["worker_s"]), daemon=True,
                         name="export").start()
        threading.Thread(target=self._loop, args=(self.poll_tick, self.cfg["rcs"]["poll_s"]), daemon=True,
                         name="rcs-poll").start()

    def _loop(self, fn, every):
        while not self._stop.is_set():
            try:
                fn()
            except Exception:
                log.exception("%s lỗi", fn.__name__)
            self._stop.wait(every)

    def stop(self):
        self._stop.set()
        self.outbox.stop()

    def status(self) -> dict:
        def cnt(coll, field="status"):
            return {r["_id"]: r["n"] for r in coll.aggregate([{"$group": {"_id": f"${field}", "n": {"$sum": 1}}}])}
        return {"version": VERSION, "started_at": clean(self.started_at), "rcs_ok": self.rcs_ok,
                "moves": cnt(self.moves), "identify": cnt(self.id_orders), "export": cnt(self.do_orders),
                "outbox": cnt(self.db["outbox"]), "locations": cnt(self.locs, "state"),
                "alerts": self.alerts.count_documents({"t": {"$gte": now() - timedelta(hours=24)}})}


# ====================================================================== HTTP
def site_redirect(req: Request) -> RedirectResponse:
    """Giao diện đã gom về site chung (python -m g8b.web, cùng máy, cổng theo config/web.json)."""
    return RedirectResponse(f"http://{req.url.hostname}:{site_port()}/")


def build_app(w: WCS) -> FastAPI:
    app = FastAPI(title="G8 WCS B", version=VERSION)
    app.mount("/static", StaticFiles(directory=os.path.join(ROOT, "g8b", "static")), name="static")
    static = os.path.join(ROOT, "g8b", "static")

    async def body_of(req: Request, peer: str) -> dict:
        try:
            b = await req.json()
        except Exception:
            b = {}
        if isinstance(b, dict) and isinstance(b.get("Data"), dict):  # chấp nhận cả dạng {Auth, Data}
            b = b["Data"]
        w.msg.add("IN", peer or (req.client.host if req.client else ""), req.url.path, b)
        return b if isinstance(b, (dict, list)) else {}

    def reply(req: Request, peer: str, resp: dict) -> JSONResponse:
        w.msg.add("ACK", peer, req.url.path, None, 200, resp)
        return JSONResponse(resp)

    # --- WMS -> WCS (giống WCS gốc)
    @app.post("/move-service/task/wms/move_pallet")
    async def wms_move(req: Request):
        b = await body_of(req, "WMS")
        return reply(req, "WMS", w.move_create(b))

    @app.post("/identify-service/wms/task/identify-cartons")
    async def wms_identify(req: Request):
        b = await body_of(req, "WMS")
        return reply(req, "WMS", w.identify_create(b, "Product"))

    @app.post("/identify-service/wms/task/identify-import")
    async def wms_identify_import(req: Request):
        b = await body_of(req, "WMS")
        return reply(req, "WMS", w.identify_create(b, "Import"))

    @app.post("/identify-service/wms/task/identify-cartons/cancel")
    async def wms_identify_cancel(req: Request):
        b = await body_of(req, "WMS")
        return reply(req, "WMS", w.identify_cancel(b))

    @app.post("/export-service/task/wms/export_items")
    async def wms_export(req: Request):
        b = await body_of(req, "WMS")
        return reply(req, "WMS", w.export_create(b))

    # --- thiết bị -> WCS
    @app.post("/identify-service/ids/pallet_identify_done")
    async def ids_done(req: Request):
        peer = req.client.host if req.client else ""
        b = await body_of(req, "IDS " + peer)
        return reply(req, "IDS", w.pallet_done(b, peer))

    @app.post("/export-service/scs/taskStatusReport")
    async def scs_report(req: Request):
        peer = req.client.host if req.client else ""
        b = await body_of(req, "SCS " + peer)
        return reply(req, "SCS", w.scs_report(b, peer))

    @app.post("/rcs/taskStatusReport")
    @app.post("/move-service/rcs/taskStatusReport")
    async def rcs_report(req: Request):
        b = await body_of(req, "RCS")
        b = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in b.items()} if isinstance(b, dict) else {}
        oid = str(b.get("orderId", ""))
        try:
            st = int(b.get("status"))
        except (TypeError, ValueError):
            st = -1
        if not oid.startswith(w.cfg["rcs"]["order_prefix"]) or not w.rcs_status(oid, st, b, "callback"):
            log.info("RCS callback không phải task của B: %s (bỏ qua)", oid)
        return JSONResponse(ack_dev(True))  # luôn 1000 để RCS không gửi lại

    # --- giao diện / API giám sát
    @app.get("/")
    def ui(req: Request):
        return site_redirect(req)

    @app.get("/api/dashboard")
    def api_dashboard():
        return w.dashboard()

    @app.post("/api/order/move")
    async def api_order_move(req: Request):
        return w.manual_move(await req.json())

    @app.post("/api/order/raw")
    async def api_order_raw(req: Request):
        b = await req.json()
        return w.raw_order(b.get("kind", ""), b.get("body"))

    @app.get("/api/config")
    def api_config():
        return w.config_view()

    @app.put("/api/config")
    async def api_config_save(req: Request):
        try:
            return w.config_save(await req.json())
        except Exception as e:
            log.exception("lưu cấu hình lỗi")
            return {"ok": False, "msg": str(e)}

    @app.get("/api/status")
    def api_status():
        return w.status()

    @app.get("/api/moves")
    def api_moves(status: str = "", limit: int = 200):
        q = {"status": status} if status else {}
        return clean(list(w.moves.find(q, {"history": 0}).sort("created_at", DESCENDING).limit(limit)))

    @app.get("/api/moves/{task_id:path}")
    def api_move(task_id: str):
        return clean(w.moves.find_one({"task_id": task_id}) or {})

    @app.post("/api/moves/action")
    async def api_move_action(req: Request):
        b = await req.json()
        fn = {"cancel": w.move_cancel, "complete": w.move_complete, "retry": w.move_retry}.get(b.get("action"))
        res = fn(b.get("task_id", "")) if fn else {"ok": False, "msg": "action?"}
        log.info("Thao tác tay %s %s -> %s", b.get("action"), b.get("task_id"), res)
        return res

    @app.get("/api/identify")
    def api_identify(limit: int = 100):
        orders = clean(list(w.id_orders.find().sort("created_at", DESCENDING).limit(limit)))
        for o in orders:
            o["pallets"] = clean(list(w.id_pallets.find({"order_key": o["key"]}).sort("created_at", ASCENDING)))
        return orders

    @app.get("/api/export")
    def api_export(limit: int = 100):
        return clean(list(w.do_orders.find({}, {"payload": 0}).sort("created_at", DESCENDING).limit(limit)))

    @app.post("/api/export/close")
    async def api_export_close(req: Request):
        b = await req.json()
        return w.export_close(b.get("do_id"))

    @app.get("/api/lines")
    def api_lines():
        return clean(list(w.lines.find().sort("line_id", ASCENDING)))

    @app.post("/api/lines/enable")
    async def api_line_enable(req: Request):
        b = await req.json()
        r = w.lines.update_one({"line_id": b.get("line_id")}, {"$set": {"enabled": bool(b.get("enabled"))}})
        log.info("Line %s enabled=%s (cutover)", b.get("line_id"), b.get("enabled"))
        return {"ok": r.matched_count == 1}

    @app.get("/api/locations")
    def api_locations(zone: str = "", state: str = ""):
        q: dict = {}
        if zone:
            q["zone"] = zone
        if state:
            q["state"] = state
        return clean(list(w.locs.find(q).sort("code", ASCENDING).limit(5000)))

    @app.post("/api/locations/set")
    async def api_location_set(req: Request):
        b = await req.json()
        st = b.get("state")
        if st not in (L.EMPTY, L.FULL, L.LOCKED):
            return {"ok": False, "msg": "state phải là EMPTY / FULL / LOCKED"}
        r = w.locs.update_one({"code": L.norm(b.get("code")), "state": {"$ne": L.BUSY}},
                              {"$set": {"state": st, "pallet_id": b.get("pallet_id") if st == L.FULL else None}})
        return {"ok": r.modified_count == 1 or r.matched_count == 1}


    L.add_routes(app, w.locstore, lambda: save_config(w.cfg), manage_warehouses=False)

    @app.get("/api/outbox")
    def api_outbox(status: str = "", limit: int = 200):
        q = {"status": status} if status else {}
        return clean(list(w.db["outbox"].find(q).sort("created_at", DESCENDING).limit(limit)))

    @app.post("/api/outbox/retry")
    async def api_outbox_retry(req: Request):
        b = await req.json()
        return {"ok": w.outbox.retry(b.get("key", ""))}

    @app.get("/api/alerts")
    def api_alerts(limit: int = 200):
        return clean(list(w.alerts.find().sort("t", DESCENDING).limit(limit)))

    @app.get("/api/messages")
    def api_messages(limit: int = 300, q: str = ""):
        f = {"$or": [{"path": {"$regex": q, "$options": "i"}}, {"peer": {"$regex": q, "$options": "i"}}]} if q else {}
        return clean(list(w.db["messages"].find(f).sort("t", DESCENDING).limit(limit)))

    return app


def main():
    cfg = load_config("wcs", DEFAULTS)
    setup_logging("wcs", os.environ.get("G8B_LOG_DIR") and os.path.join(os.environ["G8B_LOG_DIR"], "wcs"))
    log.info("WCS B v%s - cấu hình %s", VERSION, cfg["_path"])
    w = WCS(cfg)
    w.start()
    w.locstore.watch()
    app = build_app(w)
    log.info("Nghe tại %s:%s", cfg["server"]["host"], cfg["server"]["port"])
    uvicorn.run(app, host=cfg["server"]["host"], port=cfg["server"]["port"], log_level="warning")


if __name__ == "__main__":
    main()
