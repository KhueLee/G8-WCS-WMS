"""Simulator cho môi trường test luồng B.

  python -m g8b.sim rcs --port 7100 --callback http://127.0.0.1:9000/rcs/taskStatusReport
  python -m g8b.sim ids --port 9501 --line MCSX009 --done-url http://127.0.0.1:9000/identify-service/ids/pallet_identify_done
  python -m g8b.sim scs --port 9701 --line BCXB009 --report-url http://127.0.0.1:9000/export-service/scs/taskStatusReport

rcs: ICS HuaRay V3.0.6 (addTask, cancelTask, getTaskOrderStatus, endTask, callback 3.2.1), nhiều robot,
     tiêm lỗi (status 7), callback gửi tới MỌI URL đã khai (giống trường hợp RCS khai nhiều URL).
ids: hành vi bàn định danh G8-ID (DOCKING_PROTOCOL IF-01..05): 1 pallet / lần, gửi lại pallet_done mỗi retry_s.
scs: hành vi đầu xuất G8-Export: /scs/orderTask, báo từng pallet đủ Ctn_Need về UrlWms.
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import os
import threading
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .common import ci_get, http_post, setup_logging

log = logging.getLogger("sim")


async def read_json(req: Request) -> Any:
    raw = await req.body()
    try:
        return json.loads(raw.decode("utf-8-sig") or "null")
    except Exception:
        return None


def state_page(title: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><meta charset=utf-8><title>{title}</title>
<style>body{{font:13px system-ui;margin:12px;background:#111;color:#ddd}}pre{{white-space:pre-wrap}}</style>
<h3>{title}</h3><pre id=s></pre><script>
async function r(){{const x=await fetch('/sim/state');document.getElementById('s').textContent=
JSON.stringify(await x.json(),null,1)}};r();setInterval(r,1000)</script>""")


# ====================================================================== RCS
class RcsSim:
    TERMINAL = (3, 7, 8)

    def __init__(self, a):
        self.callbacks = [u for u in (a.callback or []) if u]
        self.robots = [f"F{i:03d}" for i in range(1, a.robots + 1)]
        self.step_s = a.step_s
        self.templates = set(a.templates.split(","))
        self.points: set[str] | None = None
        self.points_file, self.extra_points, self._pmtime = a.points, a.extra_points, None
        self._load_points()
        self.area_id = a.area_id
        self.tasks: dict[str, dict] = {}
        self.busy: dict[str, str] = {}
        self.fail_next = 0
        self.fail_orders: set[str] = set()
        self.callback_on = True
        self.seq = itertools.count(1)
        self.lock = threading.RLock()
        threading.Thread(target=self.loop, daemon=True).start()

    def _load_points(self):
        """Danh sách điểm hợp lệ = file vị trí (đọc lại khi file đổi) + điểm sàn khai thêm."""
        if not self.points_file or not os.path.exists(self.points_file):
            return
        m = os.path.getmtime(self.points_file)
        if m == self._pmtime:
            return
        from .locations import read_highbay
        self.points = {r["code"] for r in read_highbay(self.points_file)}
        self.points |= {p.strip().upper() for p in (self.extra_points or "").split(",") if p.strip()}
        self._pmtime = m

    def add(self, b: dict) -> dict:
        self._load_points()
        oid = str(b.get("orderId") or "")
        tpl = b.get("modelProcessCode")
        if not oid or not tpl:
            return {"code": 7201, "data": "", "desc": "Incorrect request parameter"}
        if tpl not in self.templates:
            return {"code": 2103, "data": "", "desc": "Task process template number does not exist"}
        det = (b.get("taskOrderDetail") or [{}])[0]
        pts = [p.strip() for p in str(det.get("taskPath", "")).split(",") if p.strip()]
        if len(pts) != 2 or (self.points is not None and any(p not in self.points for p in pts)):
            return {"code": 2108, "data": "", "desc": "Incorrect collection of task points"}
        with self.lock:
            if oid in self.tasks:
                return {"code": 2111, "data": "", "desc": "Please do not repeatedly assign tasks"}
            fail = False
            if self.fail_next > 0:
                self.fail_next -= 1
                fail = True
            self.tasks[oid] = {"orderId": oid, "tpl": tpl, "priority": int(b.get("priority") or 6),
                               "fromSystem": b.get("fromSystem", ""), "path": pts, "status": 1, "sub": 0,
                               "robot": "", "created": time.time(), "next": time.time(), "pos": pts[0],
                               "fail": fail or oid in self.fail_orders, "seq": next(self.seq)}
        log.info("addTask %s %s %s", oid, tpl, pts)
        return {"code": 1000, "data": oid, "desc": "Request successfully"}

    def cancel(self, b) -> dict:
        items = b if isinstance(b, list) else [b]
        for it in items:
            oid = str((it or {}).get("orderId") or "")
            with self.lock:
                t = self.tasks.get(oid)
                if t and t["status"] not in self.TERMINAL:
                    self.finish(t, 3)
        return {"code": 1000, "desc": "success"}

    def status_of(self, oid: str) -> dict:
        with self.lock:
            t = self.tasks.get(oid)
            if not t:
                return {"code": 1001, "data": {}, "desc": "order detail is lost"}
            return {"code": 1000, "desc": "Request successfully", "data": {
                "areaId": self.area_id, "createTime": int(t["created"]), "fromSystem": t["fromSystem"],
                "status": t["status"], "taskOrderDetail": [{
                    "deviceNum": t["robot"], "qrContent": t["pos"], "subTaskStatus": 3, "subTaskSeq": t["sub"],
                    "time": int(time.time() * 1000), "deviceCode": t["robot"], "subTaskTypeId": 5,
                    "subTaskId": t["seq"], "status": str(t["status"])}]}}

    def finish(self, t: dict, status: int, err: str = ""):
        t["status"] = status
        if t["robot"]:
            self.busy.pop(t["robot"], None)
        self.report(t, err)

    def report(self, t: dict, err: str = ""):
        if not self.callback_on:
            return
        body = {"orderId": t["orderId"], "status": t["status"], "qrContent": t["pos"], "deviceNum": t["robot"],
                "deviceCode": t["robot"], "storageNum": "", "modelProcessCode": t["tpl"], "errorDesc": err,
                "shelfNumber": "", "shelfCurrPosition": t["pos"], "subTaskStatus": "3",
                "subTaskTypeId": "5", "subTaskId": str(t["seq"]), "subTaskSeq": str(t["sub"]),
                "icsTaskOrderDetailId": str(t["seq"]), "processRate": f"{t['sub']}/3"}
        for url in self.callbacks:
            threading.Thread(target=self._post_retry, args=(url, body), daemon=True).start()

    @staticmethod
    def _post_retry(url, body):
        for _ in range(5):  # RCS gửi lại tới khi nhận 1000
            st, resp, err = http_post(url, body, 5)
            if st == 200 and isinstance(resp, dict) and resp.get("code") == 1000:
                return
            time.sleep(1)

    def loop(self):
        while True:
            time.sleep(0.05)
            nowt = time.time()
            with self.lock:
                waiting = sorted((t for t in self.tasks.values() if t["status"] == 1),
                                 key=lambda x: (x["priority"], x["seq"]))
                for t in waiting:
                    free = [r for r in self.robots if r not in self.busy]
                    if not free:
                        break
                    t["robot"] = free[0]
                    self.busy[free[0]] = t["orderId"]
                    t["status"], t["next"] = 9, nowt + self.step_s
                    self.report(t)
                for t in list(self.tasks.values()):
                    if t["status"] in self.TERMINAL or t["status"] == 1 or nowt < t["next"]:
                        continue
                    t["next"] = nowt + self.step_s
                    if t["status"] == 9:
                        t["status"], t["sub"] = 6, 1
                        self.report(t)
                    elif t["status"] == 6 and t["sub"] == 1:
                        t["sub"] = 2  # đã nhấc hàng ở điểm đầu
                        if t["fail"]:
                            self.finish(t, 7, "Simulated failure: pallet identify error 25216")
                    elif t["status"] == 6 and t["sub"] == 2:
                        t["sub"], t["pos"] = 3, t["path"][1]
                        self.finish(t, 8)

    def app(self) -> FastAPI:
        app = FastAPI(title="RCS simulator")

        @app.post("/ics/taskOrder/addTask")
        async def add(req: Request):
            return self.add(await read_json(req) or {})

        @app.post("/ics/out/task/cancelTask")
        async def cancel(req: Request):
            return self.cancel(await read_json(req) or {})

        @app.post("/ics/out/endTask")
        async def end(req: Request):
            b = await read_json(req) or {}
            return self.cancel(b) | {"data": ""}

        @app.post("/ics/out/task/getTaskOrderStatus")
        async def st(req: Request):
            b = await read_json(req) or {}
            return self.status_of(str(b.get("orderId", "")))

        @app.post("/sim/fail")
        async def fail(req: Request):
            b = await read_json(req) or {}
            with self.lock:
                self.fail_next += int(b.get("next", 0))
                if b.get("orderId"):
                    self.fail_orders.add(b["orderId"])
            return {"ok": True, "fail_next": self.fail_next}

        @app.post("/sim/callback")
        async def cb(req: Request):
            b = await read_json(req) or {}
            self.callback_on = bool(b.get("on", True))
            return {"ok": True, "callback_on": self.callback_on}

        @app.get("/sim/state")
        def state():
            with self.lock:
                return {"callbacks": self.callbacks, "callback_on": self.callback_on, "busy": self.busy,
                        "tasks": sorted(self.tasks.values(), key=lambda x: -x["seq"])[:100]}

        @app.get("/")
        def ui():
            return state_page("RCS simulator")
        return app


# ====================================================================== IDS (bàn định danh)
class IdsSim:
    def __init__(self, a):
        self.line, self.done_url = a.line, a.done_url
        self.per_carton_s, self.retry_s = a.per_carton_s, a.retry_s
        self.pallets: list[dict] = []        # WAIT / RUN / SENDING
        self.history: list[dict] = []
        self.confirms: list[dict] = []
        self.paused = False
        self.lock = threading.RLock()
        threading.Thread(target=self.loop, daemon=True).start()

    def order(self, b) -> tuple[int, dict]:
        if not isinstance(b, dict):
            return 400, {"code": 1001, "desc": "Invalid JSON"}
        try:
            po = int(ci_get(b, "PO_ID", default=0))
        except (TypeError, ValueError):
            po = 0
        if po <= 0:
            return 400, {"code": 1001, "desc": "order_id invalid"}
        det = ci_get(b, "info_detail", "InforDetail", "infor_detail")
        if not isinstance(det, list) or not det:
            return 400, {"code": 1001, "desc": "Lệnh sản xuất không có dữ liệu chi tiết."}
        with self.lock:
            for it in det:
                pid = ci_get(it, "Pallet_ID", "PalletId")
                if not pid:
                    return 400, {"code": 1001, "desc": "Thiếu Pallet_ID trong info_detail"}
                if not ci_get(it, "Carton_List", "CartonList"):
                    return 400, {"code": 1001, "desc": f"Pallet {pid} không có Carton_List"}
            for it in det:
                pid = ci_get(it, "Pallet_ID", "PalletId")
                if any(p["pallet_id"] == pid for p in self.pallets):
                    continue  # như G8-ID: pallet trùng bị bỏ qua, vẫn 200
                self.pallets.append({"task_id": ci_get(b, "Task_ID", default=""), "po_id": po,
                                     "po_name": ci_get(b, "PO_Name", default=""), "wh": ci_get(b, "WH_Code", default=""),
                                     "line": ci_get(b, "Line_ID", default=self.line), "pallet_id": pid,
                                     "ctn": len(ci_get(it, "Carton_List", "CartonList")), "state": "WAIT",
                                     "t": 0.0})
        return 200, {"code": 1000, "desc": "SUCCESS"}

    def cancel(self, b) -> tuple[int, dict]:
        keys = {k: ci_get(b or {}, k) for k in ("Task_ID", "PO_ID", "Pallet_ID")}
        if not any(keys.values()):
            return 400, {"code": 1001, "desc": "Task_ID invalid"}
        with self.lock:
            pos = {p["po_id"] for p in self.pallets
                   if p["task_id"] == keys["Task_ID"] or str(p["po_id"]) == str(keys["PO_ID"])
                   or p["pallet_id"] == keys["Pallet_ID"]}
            keep = []
            for p in self.pallets:
                if p["po_id"] in pos and p["state"] == "WAIT":
                    p["state"] = "CANCEL"
                    self.history.append(p)
                else:
                    keep.append(p)
            self.pallets = keep
        return 200, {"code": 1000, "desc": "SUCCESS"}

    def confirm(self, b) -> tuple[int, dict]:
        if not ci_get(b or {}, "PO_ID"):
            return 400, {"code": 1001, "desc": "PO_ID invalid"}
        if not ci_get(b, "Pallet_ID", "palletID") or not ci_get(b, "Line_ID", "LineId"):
            return 400, {"code": 1001, "desc": "Line_ID or Pallet_ID invalid"}
        with self.lock:
            self.confirms.append(b)
        return 200, {"code": 1000, "desc": "SUCCESS"}

    def loop(self):
        while True:
            time.sleep(0.1)
            with self.lock:
                if self.paused or not self.pallets:
                    continue
                p = self.pallets[0]
                if p["state"] == "WAIT":
                    p["state"], p["t"] = "RUN", time.time() + self.per_carton_s * p["ctn"]
                    continue
                if time.time() < p["t"]:
                    continue
                body = {"Task_ID": p["task_id"], "PO_ID": p["po_id"], "WH_Code": p["wh"], "Line_ID": p["line"],
                        "palletID": p["pallet_id"], "Ctn": p["ctn"]}
            st, resp, err = http_post(self.done_url, body, 10)
            with self.lock:
                if 200 <= st < 300:          # G8-ID chỉ xét HTTP 2xx
                    p["state"] = "DONE"
                    self.history.append(p)
                    if self.pallets and self.pallets[0] is p:
                        self.pallets.pop(0)
                else:
                    p["state"], p["t"] = "SENDING", time.time() + self.retry_s
                    log.warning("pallet_done %s lỗi %s %s - gửi lại sau %ss", p["pallet_id"], st, err, self.retry_s)

    def app(self) -> FastAPI:
        app = FastAPI(title=f"IDS simulator {self.line}")

        def make(fn):
            async def h(req: Request):
                code, resp = fn(await read_json(req))
                return JSONResponse(resp, status_code=code)
            return h

        for path, fn in (("orderTask", self.order), ("cancelTask", self.cancel), ("confirm_product", self.confirm)):
            h = make(fn)
            app.add_api_route(f"/ids/{path}", h, methods=["POST"])
            app.add_api_route(f"/ids/{path}/", h, methods=["POST"])

        @app.post("/sim/pause")
        async def pause(req: Request):
            b = await read_json(req) or {}
            self.paused = bool(b.get("paused", True))
            return {"paused": self.paused}

        @app.get("/sim/state")
        def state():
            with self.lock:
                return {"line": self.line, "queue": self.pallets, "history": self.history[-50:],
                        "confirms": self.confirms[-50:]}

        @app.get("/")
        def ui():
            return state_page(f"IDS simulator {self.line}")
        return app


# ====================================================================== SCS (đầu xuất)
class ScsSim:
    def __init__(self, a):
        self.line, self.report_url = a.line, a.report_url
        self.per_carton_s, self.retry_s = a.per_carton_s, a.retry_s
        self.orders: list[dict] = []
        self.reports: list[dict] = []
        self.lock = threading.RLock()
        threading.Thread(target=self.loop, daemon=True).start()

    def order(self, b) -> dict:
        if not isinstance(b, dict):
            return {"code": 1001, "desc": "fail", "message": "invalid json"}
        items = ci_get(b, "Item_Information", default=[]) or []
        if not items:
            return {"code": 1001, "desc": "fail", "message": "Item_Information rỗng"}
        with self.lock:
            for it in sorted(items, key=lambda x: int(ci_get(x, "STT", default=0) or 0)):
                need = int(ci_get(it, "Ctn_Need", default=0) or 0)
                self.orders.append({"DO_ID": ci_get(b, "DO_ID"), "WH_Code": ci_get(b, "WH_Code", default=""),
                                    "Conveyor_Belt": ci_get(b, "Conveyor_Belt", default=self.line),
                                    "Pallet_ID": ci_get(it, "Pallet_ID"), "Item_Code": ci_get(it, "Item_Code"),
                                    "need": need, "cartons": (ci_get(it, "Carton_List", default=[]) or [])[:need],
                                    "t": 0.0, "state": "WAIT"})
        return {"code": 1000, "desc": "success"}

    def loop(self):
        while True:
            time.sleep(0.1)
            with self.lock:
                if not self.orders:
                    continue
                o = self.orders[0]
                if o["state"] == "WAIT":
                    o["state"], o["t"] = "SCAN", time.time() + self.per_carton_s * max(o["need"], 1)
                    continue
                if time.time() < o["t"]:
                    continue
                body = {"DO_ID": o["DO_ID"], "WH_Code": o["WH_Code"], "Conveyor_Belt": o["Conveyor_Belt"],
                        "Pallet_ID": o["Pallet_ID"], "Item_Code": o["Item_Code"], "Ctn": o["need"],
                        "Ctn_Need": o["need"], "Carton_List": o["cartons"]}
            st, resp, err = http_post(self.report_url, body, 8,
                                      headers={"Idempotency-Key": f"{o['DO_ID']}:{o['Pallet_ID']}"})
            with self.lock:
                if 200 <= st < 300:
                    o["state"] = "DONE"
                    self.reports.append(body)
                    self.orders.pop(0)
                else:
                    o["t"] = time.time() + self.retry_s

    def app(self) -> FastAPI:
        app = FastAPI(title=f"SCS simulator {self.line}")

        @app.post("/scs/orderTask")
        @app.post("/scs/orderTask/")
        async def order(req: Request):
            return self.order(await read_json(req))

        @app.get("/sim/state")
        def state():
            with self.lock:
                return {"line": self.line, "queue": self.orders, "reports": self.reports[-50:]}

        @app.get("/")
        def ui():
            return state_page(f"SCS simulator {self.line}")
        return app


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="kind", required=True)
    r = sub.add_parser("rcs")
    r.add_argument("--port", type=int, default=7100)
    r.add_argument("--callback", action="append", help="URL nhận callback, khai nhiều lần được")
    r.add_argument("--robots", type=int, default=3)
    r.add_argument("--step-s", type=float, default=1.0)
    r.add_argument("--templates", default="forkAutoTemplate2,liftMoveShelf2,G8ManualMove")
    r.add_argument("--points", default="", help="file highbay: chỉ chấp nhận điểm có trong file")
    r.add_argument("--extra-points", default="", help="điểm thêm ngoài file (điểm sàn), cách nhau dấu phẩy")
    r.add_argument("--area-id", type=int, default=2)
    i = sub.add_parser("ids")
    i.add_argument("--port", type=int, default=9501)
    i.add_argument("--line", default="MCSX009")
    i.add_argument("--done-url", default="http://127.0.0.1:9000/identify-service/ids/pallet_identify_done")
    i.add_argument("--per-carton-s", type=float, default=0.5)
    i.add_argument("--retry-s", type=float, default=30)
    s = sub.add_parser("scs")
    s.add_argument("--port", type=int, default=9701)
    s.add_argument("--line", default="BCXB009")
    s.add_argument("--report-url", default="http://127.0.0.1:9000/export-service/scs/taskStatusReport")
    s.add_argument("--per-carton-s", type=float, default=0.3)
    s.add_argument("--retry-s", type=float, default=20)
    a = ap.parse_args()
    name = f"sim-{a.kind}-{a.port}"
    setup_logging(name, os.environ.get("G8B_LOG_DIR") and os.path.join(os.environ["G8B_LOG_DIR"], name))
    sim = {"rcs": RcsSim, "ids": IdsSim, "scs": ScsSim}[a.kind](a)
    log.info("%s nghe cổng %s", a.kind, a.port)
    uvicorn.run(sim.app(), host="0.0.0.0", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
