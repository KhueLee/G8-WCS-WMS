"""Test đầu cuối luồng B: WMS ảo + WCS B + RCS sim + IDS sim + SCS sim, dựng trên cổng riêng, DB riêng.

Chạy: .venv\\Scripts\\python tests\\e2e_test.py      (cần MongoDB ở 127.0.0.1:27017)
Kết quả: in PASS / FAIL từng kịch bản, log ở logs\\e2e\\.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback

import httpx
from pymongo import MongoClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
P = {"wcs": 19000, "wms": 19100, "rcs": 17100, "ids": 19501, "scs": 19701}
U = {k: f"http://127.0.0.1:{v}" for k, v in P.items()}
DB_WCS, DB_WMS = "e2e_g8b_wcs", "e2e_g8b_wms"
LOGDIR = os.path.join(ROOT, "logs", "e2e")
TMP = tempfile.mkdtemp(prefix="g8b_e2e_")
procs: dict[str, subprocess.Popen] = {}
results: list[tuple[str, bool, str]] = []
http = httpx.Client(timeout=10, trust_env=False)


def write_cfg():
    locs = {"zones": [{"zone": "B", "kind": "RACK", "ranges": [{"rack": "S02", "cols": [250, 259], "levels": [1, 2]}]},
                      {"zone": "STATION", "kind": "STATION", "codes": ["BDD09", "BCXB01"]}]}
    with open(os.path.join(TMP, "locations.json"), "w", encoding="utf-8") as f:
        json.dump(locs, f)
    wcs = {"server": {"port": P["wcs"]}, "mongo": {"db": DB_WCS},
           "wms": {"url": U["wms"]},
           "rcs": {"url": U["rcs"], "poll_s": 1.0},
           "locations": {"file": os.path.join(TMP, "locations.json")},
           "identify_lines": [{"line_id": "MCSX009", "url": U["ids"] + "/ids", "location": "BDD09"}],
           "export_lines": [{"line_id": "BCXB001", "url": U["scs"] + "/scs", "location": "BCXB01"}],
           "outbox": {"retry_s": 1}, "worker_s": 0.3}
    wms = {"server": {"port": P["wms"]}, "mongo": {"db": DB_WMS}, "wcs": {"url": U["wcs"]},
           "locations": {"file": os.path.join(TMP, "locations.json")},
           "stations": {"MCSX009": "BDD09", "BCXB001": "BCXB01"}, "outbox": {"retry_s": 1}}
    for name, c in (("wcs", wcs), ("wms", wms)):
        with open(os.path.join(TMP, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(c, f)


def start(name: str):
    env = dict(os.environ, G8B_WCS_CONFIG=os.path.join(TMP, "wcs.json"), G8B_WMS_CONFIG=os.path.join(TMP, "wms.json"),
               G8B_LOG_DIR=LOGDIR, PYTHONUTF8="1")
    cmd = {
        "wcs": [PY, "-m", "g8b.wcs"],
        "wms": [PY, "-m", "g8b.wms"],
        "rcs": [PY, "-m", "g8b.sim", "rcs", "--port", str(P["rcs"]), "--step-s", "0.3", "--robots", "2",
                "--callback", U["wcs"] + "/rcs/taskStatusReport",
                "--callback", U["wms"] + "/not-a-wcs-callback",          # URL thứ 2 (luồng A giả) - RCS gửi mọi URL
                "--points", os.path.join(TMP, "locations.json")],
        "ids": [PY, "-m", "g8b.sim", "ids", "--port", str(P["ids"]), "--line", "MCSX009", "--per-carton-s", "0.1",
                "--retry-s", "1", "--done-url", U["wcs"] + "/identify-service/ids/pallet_identify_done"],
        "scs": [PY, "-m", "g8b.sim", "scs", "--port", str(P["scs"]), "--line", "BCXB001", "--per-carton-s", "0.05",
                "--retry-s", "1", "--report-url", U["wcs"] + "/export-service/scs/taskStatusReport"],
    }[name]
    os.makedirs(LOGDIR, exist_ok=True)
    out = open(os.path.join(LOGDIR, f"{name}.stdout.log"), "a", encoding="utf-8")
    procs[name] = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT)
    wait_up(name)


def stop(name: str):
    p = procs.pop(name, None)
    if p:
        p.kill()
        p.wait(10)


def wait_up(name: str, timeout: float = 30):
    path = {"wcs": "/api/status", "wms": "/api/config"}.get(name, "/sim/state")
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if http.get(U[name] + path).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.3)
    raise RuntimeError(f"{name} không lên")


def get(name, path):
    return http.get(U[name] + path).json()


def post(name, path, body):
    r = http.post(U[name] + path, json=body)
    return r.json()


def until(fn, timeout=30, every=0.3, what=""):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            last = fn()
            if last:
                return last
        except Exception as e:
            last = e
        time.sleep(every)
    raise AssertionError(f"hết giờ chờ: {what} (giá trị cuối: {last})")


def scenario(fn):
    def run():
        t0 = time.time()
        try:
            fn()
            results.append((fn.__name__, True, f"{time.time() - t0:.1f}s"))
            print(f"PASS {fn.__name__} ({time.time() - t0:.1f}s)", flush=True)
        except Exception as e:
            results.append((fn.__name__, False, f"{e}"))
            print(f"FAIL {fn.__name__}: {e}", flush=True)
            traceback.print_exc()
    run.__name__ = fn.__name__
    return run


def wms_order(oid):
    return next((o for o in get("wms", "/api/orders") if o["order_id"] == oid), None)


def wcs_moves_of(order):
    ids = {t["Task_ID"] for t in order["tasks"]}
    return [m for m in get("wcs", "/api/moves") if m["task_id"] in ids]


def loc(name, code):
    return next((x for x in get(name, "/api/locations") if x["code"] == code), None)


# ---------------------------------------------------------------------- kịch bản
@scenario
def s01_move_group_success():
    r = post("wms", "/api/move", {"pallet_id": "LPN-T01", "type_robot": 1, "type_move": 11,
                                  "steps": [{"from": "BDD09", "to": "S02-250-1"}, {"from": "S02-250-1", "to": "S02-251-1"}]})
    oid = r["order_id"]
    until(lambda: wms_order(oid)["status"] == "COMPLETED", what="WMS nhận đủ 2 confirm success")
    o = wms_order(oid)
    assert set(o["results"].values()) == {"success"}, o["results"]
    ms = wcs_moves_of(o)
    assert all(m["status"] == "SUCCEEDED" and m["reported"] is True for m in ms), ms
    assert all(m["order_id"].startswith("VB-") and "MovePallet" not in m["order_id"] for m in ms)
    until(lambda: loc("wcs", "S02-251-1")["state"] == "FULL" and loc("wcs", "S02-250-1")["state"] == "EMPTY",
          what="ô WCS cập nhật")
    assert loc("wms", "S02-251-1")["pallet_id"] == "LPN-T01"


@scenario
def s02_move_fail_skip_then_retry():
    post("rcs", "/sim/fail", {"next": 1})
    r = post("wms", "/api/move", {"pallet_id": "LPN-T02", "type_robot": 2, "type_move": 11,
                                  "steps": [{"from": "BDD09", "to": "S02-252-1"}, {"from": "S02-252-1", "to": "S02-253-1"}]})
    oid = r["order_id"]
    until(lambda: wms_order(oid)["status"] == "FAILED" and len(wms_order(oid)["results"]) == 2, what="2 báo false")
    o = wms_order(oid)
    ms = sorted(wcs_moves_of(o), key=lambda m: m["n"])
    assert [m["status"] for m in ms] == ["FAILED", "SKIPPED"], [m["status"] for m in ms]
    assert loc("wcs", "S02-252-1")["state"] == "EMPTY", "ô đích phải được nhả khi task lỗi"
    res = post("wcs", "/api/moves/action", {"action": "retry", "task_id": ms[0]["task_id"]})
    assert res["ok"] and res["count"] == 2, res
    until(lambda: set(wms_order(oid)["results"].values()) == {"success"}, what="chạy lại thành công")


@scenario
def s03_unknown_location_and_bad_robot():
    r = post("wms", "/api/move", {"pallet_id": "LPN-T03", "type_robot": 1, "steps": [{"from": "BDD09", "to": "X99-1-1"}]})
    until(lambda: wms_order(r["order_id"])["status"] == "FAILED", what="vị trí lạ bị báo false")
    m = wcs_moves_of(wms_order(r["order_id"]))[0]
    assert m["reason"] == "LocationFailed", m
    r = post("wms", "/api/move", {"pallet_id": "LPN-T03", "type_robot": 9, "steps": [{"from": "BDD09", "to": "S02-254-1"}]})
    until(lambda: wms_order(r["order_id"])["status"] == "FAILED", what="Type_Robot sai bị báo false")


@scenario
def s04_duplicate_task_id():
    body = {"Task_ID": "1/1_MovePallet_dup", "WH_Code": "WH_TB", "Pallet_ID": "LPN-T04", "Type_Robot_ID": 1,
            "Type_Move_ID": 11, "Location_Old": "BDD09", "Location_New": "S02-255-1"}
    assert post("wcs", "/move-service/task/wms/move_pallet", body)["code"] == 200
    assert post("wcs", "/move-service/task/wms/move_pallet", body)["code"] == 200, "gửi lặp y hệt phải 200"
    assert post("wcs", "/move-service/task/wms/move_pallet", dict(body, Pallet_ID="X"))["code"] == 201
    miss = post("wcs", "/move-service/task/wms/move_pallet", {"Task_ID": "1/1_MovePallet_x"})
    assert miss["code"] == 201 and "Missing" in miss["desc"], miss


@scenario
def s05_identify_po_full_flow():
    r = post("wms", "/api/identify", {"kind": "PO", "line_id": "MCSX009", "pallets": 2, "cartons": 3})
    po = r["po_id"]
    pallets = [i["Pallet_ID"] for i in r["body"]["Item_Information"]]
    until(lambda: next(o for o in get("wcs", "/api/identify") if o["po_id"] == po)["status"] == "COMPLETED",
          timeout=40, what="WCS hoàn tất PO")
    st = get("ids", "/sim/state")
    conf = [c for c in st["confirms"] if c.get("PO_ID") == po]
    assert len(conf) == 2 and all(c["Status"] == "true" and c["Location"].startswith("S02-") for c in conf), conf
    # WMS tự tạo lệnh cất kệ -> pallet về ô đã gán
    until(lambda: all(p["status"] == "STORED" for p in get("wms", "/api/pallets") if p["pallet_id"] in pallets),
          timeout=40, what="pallet được cất vào kệ")
    for p in get("wms", "/api/pallets"):
        if p["pallet_id"] in pallets:
            assert p["location"] == p["assigned_location"], p


@scenario
def s06_identify_asn():
    r = post("wms", "/api/identify", {"kind": "ASN", "line_id": "MCSX009", "pallets": 1, "cartons": 2})
    po = r["po_id"]
    until(lambda: next(o for o in get("wcs", "/api/identify") if o["po_id"] == po)["status"] == "COMPLETED",
          timeout=30, what="ASN hoàn tất")
    o = next(o for o in get("wcs", "/api/identify") if o["po_id"] == po)
    assert o["kind"] == "Import"


@scenario
def s07_identify_cancel():
    post("ids", "/sim/pause", {"paused": True})
    r = post("wms", "/api/identify", {"kind": "PO", "line_id": "MCSX009", "pallets": 3, "cartons": 2})
    po = r["po_id"]
    until(lambda: len([p for p in get("ids", "/sim/state")["queue"] if p["po_id"] == po]) == 3, what="IDS nhận 3 pallet")
    post("wms", "/api/identify/cancel", {"po_id": po})
    until(lambda: next(o for o in get("wcs", "/api/identify") if o["po_id"] == po)["status"] == "CANCELLED",
          what="WCS hủy PO")
    until(lambda: not [p for p in get("ids", "/sim/state")["queue"] if p["po_id"] == po], what="IDS hủy pallet chờ")
    post("ids", "/sim/pause", {"paused": False})


@scenario
def s08_export_do():
    stored = [p for p in get("wms", "/api/pallets") if p["status"] == "STORED" and p["ctn_total"] >= 3][:2]
    assert len(stored) == 2, "cần 2 pallet đã cất từ s05"
    r = post("wms", "/api/export", {"belt": "BCXB001", "picks": [{"pallet_id": stored[0]["pallet_id"], "ctn_need": 2},
                                                                 {"pallet_id": stored[1]["pallet_id"]}]})
    do = r["do_id"]
    until(lambda: next(d for d in get("wcs", "/api/export") if d["do_id"] == do)["status"] == "COMPLETED",
          timeout=30, what="DO hoàn tất")
    until(lambda: wms_order(r["order_id"])["status"] == "COMPLETED", what="WMS nhận OB_Pallet")
    ps = {p["pallet_id"]: p for p in get("wms", "/api/pallets")}
    assert ps[stored[0]["pallet_id"]]["ctn_shipped"] == 2
    assert ps[stored[1]["pallet_id"]]["status"] == "SHIPPED"
    line = next(x for x in get("wcs", "/api/lines") if x["line_id"] == "BCXB001")
    assert line["busy_do"] is None, line


@scenario
def s09_wms_outage_retry():
    post("wms", "/api/test/fail_next", {"Finished_Goods": 2})
    r = post("wms", "/api/identify", {"kind": "PO", "line_id": "MCSX009", "pallets": 1, "cartons": 1})
    po = r["po_id"]
    until(lambda: next(o for o in get("wcs", "/api/identify") if o["po_id"] == po)["status"] == "COMPLETED",
          timeout=40, what="Finished_Goods gửi lại sau khi WMS hết lỗi")
    fg = [m for m in get("wcs", "/api/messages?q=Finished_Goods&limit=50") if m["dir"] == "OUT"]
    assert any(m["status"] == 503 for m in fg), "phải có lần WMS trả 503"


@scenario
def s10_line_not_owned_and_cutover():
    r = post("wcs", "/identify-service/wms/task/identify-cartons",
             {"PO_ID": 1, "Line_ID": "MCSX001", "Item_Information": [{"Pallet_ID": "P", "Carton_List": ["a"]}]})
    assert r["code"] == 201 and "không thuộc" in r["desc"], r
    post("wcs", "/api/lines/enable", {"line_id": "MCSX009", "enabled": False})
    r = post("wcs", "/identify-service/wms/task/identify-cartons",
             {"PO_ID": 2, "Line_ID": "MCSX009", "Item_Information": [{"Pallet_ID": "P", "Carton_List": ["a"]}]})
    assert r["code"] == 201 and "tắt" in r["desc"], r
    post("wcs", "/api/lines/enable", {"line_id": "MCSX009", "enabled": True})


@scenario
def s11_foreign_rcs_callback_ignored():
    r = post("wcs", "/rcs/taskStatusReport", {"orderId": "1/2_MovePallet_2026-11-24__10:00:00.1", "status": 8})
    assert r["code"] == 1000
    r = post("wcs", "/move-service/rcs/taskStatusReport", {"orderId": "VB-9999999", "status": 8})
    assert r["code"] == 1000


@scenario
def s12_restart_wcs_mid_task_polling_only():
    post("rcs", "/sim/callback", {"on": False})       # chỉ còn kênh hỏi getTaskOrderStatus
    try:
        r = post("wms", "/api/move", {"pallet_id": "LPN-T12", "type_robot": 1,
                                      "steps": [{"from": "BDD09", "to": "S02-256-1"}]})
        until(lambda: (wcs_moves_of(wms_order(r["order_id"])) or [{}])[0].get("status") in ("DISPATCHED", "RUNNING"),
              what="task đã gửi RCS")
        stop("wcs")
        time.sleep(1.5)                               # RCS chạy xong trong lúc WCS tắt
        start("wcs")
        until(lambda: wms_order(r["order_id"])["status"] == "COMPLETED", timeout=30,
              what="WCS khởi động lại, hỏi RCS, báo WMS")
    finally:
        post("rcs", "/sim/callback", {"on": True})


@scenario
def s13_restart_wms_outbox_survives():
    stop("wms")
    body = {"Task_ID": "1/1_MovePallet_offline", "WH_Code": "WH_TB", "Pallet_ID": "LPN-T13", "Type_Robot_ID": 1,
            "Type_Move_ID": 11, "Location_Old": "BDD09", "Location_New": "S02-257-1"}
    assert post("wcs", "/move-service/task/wms/move_pallet", body)["code"] == 200
    until(lambda: get("wcs", "/api/moves/1/1_MovePallet_offline")["status"] == "SUCCEEDED", what="task xong khi WMS tắt")
    time.sleep(2)
    assert get("wcs", "/api/moves/1/1_MovePallet_offline")["reported"] is False
    start("wms")
    until(lambda: get("wcs", "/api/moves/1/1_MovePallet_offline")["reported"] is True, timeout=30,
          what="outbox gửi lại khi WMS lên")


@scenario
def s14_dest_busy_waits():
    """2 task cùng ô đích: task sau chờ (không gửi RCS) tới khi ô được nhả."""
    b1 = {"Task_ID": "1/1_MovePallet_busyA", "WH_Code": "WH_TB", "Pallet_ID": "LPN-A", "Type_Robot_ID": 1,
          "Type_Move_ID": 11, "Location_Old": "BDD09", "Location_New": "S02-258-1"}
    b2 = dict(b1, Task_ID="1/1_MovePallet_busyB", Pallet_ID="LPN-B")
    post("wcs", "/move-service/task/wms/move_pallet", b1)
    post("wcs", "/move-service/task/wms/move_pallet", b2)
    until(lambda: get("wcs", "/api/moves/1/1_MovePallet_busyA")["status"] == "SUCCEEDED", what="task A xong")
    # ô đã FULL -> task B (ô kệ phải trống) bị từ chối
    until(lambda: get("wcs", "/api/moves/1/1_MovePallet_busyB")["status"] == "FAILED", what="task B không vào ô đầy")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    for db in (DB_WCS, DB_WMS):
        MongoClient("mongodb://127.0.0.1:27017").drop_database(db)
    write_cfg()
    try:
        for n in ("rcs", "ids", "scs", "wms", "wcs"):
            start(n)
        for fn in (s01_move_group_success, s02_move_fail_skip_then_retry, s03_unknown_location_and_bad_robot,
                   s04_duplicate_task_id, s05_identify_po_full_flow, s06_identify_asn, s07_identify_cancel,
                   s08_export_do, s09_wms_outage_retry, s10_line_not_owned_and_cutover,
                   s11_foreign_rcs_callback_ignored, s12_restart_wcs_mid_task_polling_only,
                   s13_restart_wms_outbox_survives, s14_dest_busy_waits):
            fn()
    finally:
        for n in list(procs):
            stop(n)
    ok = sum(1 for r in results if r[1])
    print(f"\n==== {ok}/{len(results)} PASS ====")
    for name, good, info in results:
        print(f"{'PASS' if good else 'FAIL'}  {name}  {info}")
    sys.exit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    main()
