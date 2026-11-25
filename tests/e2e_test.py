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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from g8b import locations as L  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
P = {"wcs": 19000, "wms": 19100, "rcs": 17100, "ids": 19501, "scs": 19701, "web": 19200}
U = {k: f"http://127.0.0.1:{v}" for k, v in P.items()}
DB_WCS, DB_WMS = "e2e_g8b_wcs", "e2e_g8b_wms"
LOGDIR = os.path.join(ROOT, "logs", "e2e")
TMP = tempfile.mkdtemp(prefix="g8b_e2e_")
LOCFILE = os.path.join(TMP, "locations.xlsx")
WHFILE = os.path.join(TMP, "warehouses.json")
MONGO = os.environ.get("G8B_TEST_MONGO", "mongodb://127.0.0.1:27018")
procs: dict[str, subprocess.Popen] = {}
results: list[tuple[str, bool, str]] = []
http = httpx.Client(timeout=10, trust_env=False)


def write_cfg():
    # file highbay: S02-250..259, tầng 1-2 (điểm sàn BDD09 / BCXB01 lấy từ cấu hình line)
    L.write_highbay(LOCFILE, [L.make_row(f"S02-{c}-{lv}", str(10004706 + c - 250)) for c in range(250, 260) for lv in (1, 2)])
    # 1 kho S02 (cất thành phẩm); kho khác tự tạo khi thêm điểm có tiền tố mới
    with open(WHFILE, "w", encoding="utf-8") as f:
        json.dump({"warehouses": [{"code": "S02", "name": "Kho lắp ráp LED trụ", "prefixes": ["S02"], "wh_code": "WH_TB",
                                   "robot": 1, "store": True, "active": True}]}, f)
    wcs = {"server": {"port": P["wcs"]}, "mongo": {"uri": MONGO, "db": DB_WCS},
           "wms": {"url": U["wms"]},
           "rcs": {"url": U["rcs"], "poll_s": 1.0},
           "locations": {"file": LOCFILE, "warehouses": WHFILE},
           "identify_lines": [{"line_id": "MCSX009", "url": U["ids"] + "/ids", "location": "BDD09"}],
           "export_lines": [{"line_id": "BCXB001", "url": U["scs"] + "/scs", "location": "BCXB01"}],
           "outbox": {"retry_s": 1}, "worker_s": 0.3}
    wms = {"server": {"port": P["wms"]}, "mongo": {"uri": MONGO, "db": DB_WMS}, "wcs": {"url": U["wcs"]},
           "locations": {"file": LOCFILE, "warehouses": WHFILE},
           "stations": {"MCSX009": "BDD09", "BCXB001": "BCXB01"}, "outbox": {"retry_s": 1}}
    web = {"server": {"port": P["web"]}, "wms_url": U["wms"], "wcs_url": U["wcs"]}
    for name, c in (("wcs", wcs), ("wms", wms), ("web", web)):
        with open(os.path.join(TMP, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(c, f)


def start(name: str):
    env = dict(os.environ, G8B_WCS_CONFIG=os.path.join(TMP, "wcs.json"), G8B_WMS_CONFIG=os.path.join(TMP, "wms.json"),
               G8B_WEB_CONFIG=os.path.join(TMP, "web.json"),
               G8B_LOG_DIR=LOGDIR, PYTHONUTF8="1")
    cmd = {
        "wcs": [PY, "-m", "g8b.wcs"],
        "wms": [PY, "-m", "g8b.wms"],
        "web": [PY, "-m", "g8b.web"],
        "rcs": [PY, "-m", "g8b.sim", "rcs", "--port", str(P["rcs"]), "--step-s", "0.3", "--robots", "2",
                "--callback", U["wcs"] + "/rcs/taskStatusReport",
                "--callback", U["wms"] + "/not-a-wcs-callback",          # URL thứ 2 (luồng A giả) - RCS gửi mọi URL
                "--points", LOCFILE, "--extra-points", "BDD09,BCXB01"],
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
    path = {"wcs": "/api/status", "wms": "/api/config", "web": "/api/web"}.get(name, "/sim/state")
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


def idf(kind, ctn, item="TEST_3", line="MCSX009"):
    return post("wms", "/api/identify", {"kind": kind, "line_id": line, "item_code": item, "ctn_total": ctn})


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
    assert post("wms", "/api/products", {"item_code": "TEST_3", "item_name": "SP test 3 thùng/pallet", "inner_carton": 6,
                                         "inner_pallet": 3, "gw": 1.637})["ok"]
    r = idf("PO", 6)
    assert r["ok"] and r["pallets"] == 2 and r["po_name"].startswith("VAS-"), r
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
    r = idf("ASN", 2)
    po = r["po_id"]
    until(lambda: next(o for o in get("wcs", "/api/identify") if o["po_id"] == po)["status"] == "COMPLETED",
          timeout=30, what="ASN hoàn tất")
    o = next(o for o in get("wcs", "/api/identify") if o["po_id"] == po)
    assert o["kind"] == "Import"


@scenario
def s07_identify_cancel():
    post("ids", "/sim/pause", {"paused": True})
    r = idf("PO", 9)
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
    r = idf("PO", 1)
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


@scenario
def s15_manual_move_and_raw_order():
    """Move đặt tay trên WCS B: chạy robot, không báo WMS. Bản tin WMS dán tay chạy như WMS gọi vào."""
    r = post("wcs", "/api/order/move", {"pallet_id": "LPN-M15", "type_robot": 1,
                                        "steps": [{"from": "BDD09", "to": "S02-259-1"}, {"from": "S02-259-1", "to": "S02-259-2"}]})
    assert r["ok"] and len(r["task_ids"]) == 2, r
    until(lambda: all(get("wcs", "/api/moves/" + t)["status"] == "SUCCEEDED" for t in r["task_ids"]), what="Move tay xong")
    until(lambda: all(get("wcs", "/api/moves/" + t)["reported"] == "MANUAL" for t in r["task_ids"]), what="không báo WMS")
    assert not [m for m in get("wcs", "/api/outbox") if "MANUAL" in m["key"]], "Move tay không được tạo bản tin WMS"
    bad = post("wcs", "/api/order/move", {"pallet_id": "", "steps": []})
    assert not bad["ok"]
    raw = post("wcs", "/api/order/raw", {"kind": "cancel", "body": {"PO_ID": 123}})
    assert raw["ok"], raw


@scenario
def s16_config_on_web():
    """Sửa cấu hình qua API giao diện: thêm line, áp dụng ngay, từ chối dữ liệu sai, xoá line."""
    cfg = get("wcs", "/api/config")["config"]
    cfg2 = json.loads(json.dumps(cfg))
    cfg2["export_lines"].append({"line_id": "BCXB099", "url": "http://127.0.0.1:1/scs", "location": "BCXB01", "enabled": False})
    cfg2["rcs"]["poll_s"] = 0.8
    r = http.put(U["wcs"] + "/api/config", json=cfg2).json()
    assert r["ok"], r
    lines = {x["line_id"]: x for x in get("wcs", "/api/lines")}
    assert "BCXB099" in lines and lines["BCXB099"]["enabled"] is False
    assert get("wcs", "/api/config")["config"]["rcs"]["poll_s"] == 0.8
    saved = json.load(open(os.path.join(TMP, "wcs.json"), encoding="utf-8"))
    assert any(x["line_id"] == "BCXB099" for x in saved["export_lines"]), "phải ghi xuống file cấu hình"
    bad = json.loads(json.dumps(cfg2))
    bad["rcs"]["url"] = "ftp://x"
    assert http.put(U["wcs"] + "/api/config", json=bad).json()["ok"] is False
    assert http.put(U["wcs"] + "/api/config", json=cfg).json()["ok"]
    assert "BCXB099" not in {x["line_id"] for x in get("wcs", "/api/lines")}, "xoá line khỏi cấu hình"
    s = get("wms", "/api/settings")["config"]
    s2 = json.loads(json.dumps(s))
    s2["stations"]["MCSX099"] = "bdd09"
    assert http.put(U["wms"] + "/api/settings", json=s2).json()["ok"]
    assert get("wms", "/api/config")["stations"]["MCSX099"] == "BDD09"
    assert http.put(U["wms"] + "/api/settings", json=s).json()["ok"]


@scenario
def s17_dashboards():
    d = get("wcs", "/api/dashboard")
    assert d["tiles"]["move_done_today"] >= 1 and len(d["hourly_moves"]) == 24 and "S02" in d["occupancy"], d["tiles"]
    assert sum(d["hourly_moves"]) >= d["tiles"]["move_done_today"]
    w = get("wms", "/api/dashboard")
    assert w["tiles"]["orders_today"] >= 1 and len(w["hourly_orders"]) == 24 and w["tiles"]["zone_total"] == 20, w["tiles"]
    for port, path in ((P["wcs"], "/static/common.js"), (P["wms"], "/static/wms.js"), (P["web"], "/static/wcs.js")):
        assert http.get(f"http://127.0.0.1:{port}{path}").status_code == 200



@scenario
def s18_highbay_locations():
    """Danh sách vị trí form highbay: sửa / thêm dải / xoá / xuất Excel / tải lên; WMS tự nạp lại khi WCS sửa file."""
    import io
    from openpyxl import load_workbook
    info = get("wcs", "/api/locations/info")
    assert info["count"] == 20 and set(info["stations"]) >= {"BDD09", "BCXB01"}, info
    assert [w["code"] for w in info["warehouses"]] == ["S02"] and info["warehouses"][0]["count"] == 20
    s = get("wcs", "/api/locations/search?q=S02-25&size=10")
    assert s["total"] == 20 and len(s["rows"]) == 10 and s["rows"][0]["hb"]["node"]
    row = dict(s["rows"][0]["hb"], node="99999")
    assert post("wcs", "/api/locations/row", {"row": row, "old_code": row["code"]})["ok"]
    r = post("wcs", "/api/locations/bulk", {"rack": "S05", "col_from": 1, "col_to": 3, "lv_from": 1, "lv_to": 2,
                                            "node_from": "500", "node_step": 2})
    assert r["ok"] and "6" in r["msg"], r
    assert get("wcs", "/api/locations/info")["count"] == 26
    until(lambda: get("wms", "/api/locations/info")["count"] == 26, timeout=15, what="WMS tự nạp lại file highbay")
    assert not post("wcs", "/api/locations/row", {"row": {"code": "khong hop le"}})["ok"]
    assert post("wcs", "/api/locations/delete", {"codes": ["S05-1-1", "S05-1-2"]})["ok"]
    raw = http.get(U["wcs"] + "/api/locations/export")   # mặc định dạng RAW (như "2500 RAW.xlsx")
    rws = list(load_workbook(io.BytesIO(raw.content), read_only=True)["Sheet1"].iter_rows(values_only=True))
    assert len(rws) == 24 and rws[0][10] == "S02-250-1" and rws[0][6] == "S02" and rws[0][7] == 250 and rws[0][8] == 1, rws[0]
    assert [L.read_highbay.__name__] and len(L._parse_raw([list(r) for r in rws])) == 24
    x = http.get(U["wcs"] + "/api/locations/export?fmt=14")
    assert x.status_code == 200 and "attachment" in x.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(x.content), read_only=True)
    hdr = [c for c in next(wb["Sheet0"].iter_rows(values_only=True))]
    assert hdr[:3] == ["Area Name", "Location Code", "Node"] and len(hdr) == 14, hdr
    codes = [r[1] for r in list(wb["Sheet0"].iter_rows(values_only=True))[1:]]
    assert len(codes) == 24 and "S05-3-2" in codes and "S05-1-1" not in codes
    st = list(wb["TrangThai"].iter_rows(values_only=True))
    assert st[0][:4] == ("Location Code", "Kho", "Loại", "Trạng thái") and any(r[3] == "FULL" for r in st[1:])
    nodes = {r[1]: r[2] for r in list(wb["Sheet0"].iter_rows(values_only=True))[1:]}
    assert nodes[row["code"]] == "99999" and nodes["S05-3-1"] == "504"
    # tải lại chính file đã xuất -> danh sách giữ nguyên
    import base64
    assert "S" in [w["code"] for w in get("wms", "/api/warehouses")], "kho S tự tạo cho dải S05"
    pv = post("wcs", "/api/locations/import/preview", {"name": "x.xlsx", "b64": base64.b64encode(x.content).decode()})
    assert pv["ok"] and pv["total"] == 24, pv
    ap = post("wcs", "/api/locations/import/apply", {"token": pv["token"], "select": {g["wh"]: 0 for g in pv["groups"]}})
    assert ap["ok"] and get("wcs", "/api/locations/info")["count"] == 24, ap
    bad = post("wcs", "/api/locations/import/preview", {"name": "x.txt", "b64": "AA=="})
    assert not bad["ok"]



@scenario
def s19_import_raw_and_cascade():
    """Nạp file RAW: chọn kho + giới hạn điểm, xem khác biệt, áp dụng; điểm bị bỏ -> pallet ở đó bị xoá theo;
    tạo đủ kho theo tiền tố trong file."""
    import base64
    import io
    from openpyxl import Workbook
    # pallet tay ở S02-259-2 (sẽ bị bỏ khỏi danh sách)
    assert post("wms", "/api/pallets/create", {"item_code": "TEST_3", "ctn": 3, "location": "S02-259-2",
                                               "pallet_id": "LPN-CASCADE"})["ok"]
    wb = Workbook()
    ws = wb.active
    node = 21073200
    rows = [(r, c, lv) for r in ("S02",) for c in range(250, 260) for lv in (1, 2)] + \
           [(r, c, lv) for r in ("C24", "C25", "D32") for c in range(1, 21) for lv in (1, 2)]
    for rack, col, lv in rows:
        node += 1
        if (rack, col, lv) == ("S02", 259, 2):
            continue   # điểm bị bỏ
        ws.append([None, None, None, node, None, None, rack, col, lv, None, f"{rack}-{col}-{lv}", None,
                   [280, 1620][lv - 1], None, f"(1,2,{[280, 1620][lv - 1]})"])
    buf = io.BytesIO()
    wb.save(buf)
    pv = post("wms", "/api/locations/import/preview", {"name": "2500 RAW.xlsx", "b64": base64.b64encode(buf.getvalue()).decode()})
    assert pv["ok"] and pv["total"] == 139, pv
    groups = {g["wh"]: g for g in pv["groups"]}
    assert groups["C"]["count"] == 80 and not groups["C"]["exists"] and groups["D"]["count"] == 40, groups
    sel = {"S02": 0, "C": 50}
    d = post("wms", "/api/locations/import/diff", {"token": pv["token"], "select": sel})
    assert d["selected"] == 69 and "S02-259-2" in d["removed"], d
    assert any(x.get("pallet_id") == "LPN-CASCADE" for x in d["in_use"]), d["in_use"]
    ap = post("wms", "/api/locations/import/apply", {"token": pv["token"], "select": sel})
    assert ap["ok"] and set(ap["created_warehouses"]) >= {"C", "D"}, ap
    info = get("wms", "/api/locations/info")
    assert info["count"] == 69, info["count"]
    assert {w["code"] for w in info["warehouses"]} >= {"S02", "C", "D"}
    assert next(w for w in info["warehouses"] if w["code"] == "C")["count"] == 50
    assert not get("wms", "/api/pallets/LPN-CASCADE"), "pallet ở điểm bị bỏ phải bị xoá theo"
    until(lambda: get("wcs", "/api/locations/info")["count"] == 69, timeout=15, what="WCS nạp lại danh sách mới")
    assert loc("wcs", "S02-259-2") is None


@scenario
def s20_wms_catalog_and_manual():
    """Danh mục sản phẩm + 4 loại lệnh WMS + sửa tay pallet / ô."""
    assert not post("wms", "/api/products", {"item_code": "X", "item_name": "x", "inner_carton": 0, "inner_pallet": 1})["ok"]
    assert post("wms", "/api/products", {"item_code": "TEST_5", "item_name": "SP 5", "inner_carton": 12, "inner_pallet": 5,
                                         "gw": 2})["ok"]
    # định danh vào kho C: 7 thùng -> 2 pallet (5 + 2), ô giữ chỗ thuộc kho C
    r = post("wms", "/api/identify", {"kind": "PO", "line_id": "MCSX009", "item_code": "TEST_5", "ctn_total": 7, "dest_wh": "C"})
    assert r["ok"] and r["pallets"] == 2, r
    items = r["body"]["Item_Information"]
    assert [i["Ctn"] for i in items] == [5, 2] and all(i["Location"].startswith("C") for i in items), items
    assert all(len(i["Carton_List"]) == i["Ctn"] and i["Inner_Carton"] == 12 for i in items)
    until(lambda: all(get("wms", "/api/pallets/" + i["Pallet_ID"]).get("status") == "STORED" for i in items),
          timeout=40, what="2 pallet cất vào kho C")
    p0 = items[0]["Pallet_ID"]
    assert get("wms", "/api/pallets/" + p0)["wh"] == "C"
    # luân chuyển (robot) tới ô trống khác trong kho C
    free = next(c["code"] for c in get("wms", "/api/locations?wh=C") if c["state"] == "EMPTY" and not c.get("task"))
    mv = post("wms", "/api/move", {"pallet_id": p0, "to": free, "type_move": 11})
    assert mv["ok"], mv
    until(lambda: get("wms", "/api/pallets/" + p0)["location"] == free, timeout=30, what="robot luân chuyển pallet")
    assert loc("wms", free)["state"] == "FULL"
    # cập nhật vị trí (tay): sang ô trống khác, không tạo lệnh WCS
    free2 = next(c["code"] for c in get("wms", "/api/locations?wh=C") if c["state"] == "EMPTY" and not c.get("task"))
    n_ob = len(get("wms", "/api/outbox"))
    ul = post("wms", "/api/update_location", {"pallet_id": p0, "to": free2, "note": "kiểm kê"})
    assert ul["ok"] and get("wms", "/api/pallets/" + p0)["location"] == free2, ul
    assert loc("wms", free)["state"] == "EMPTY" and loc("wms", free2)["pallet_id"] == p0
    assert len(get("wms", "/api/outbox")) == n_ob, "cập nhật vị trí tay không gửi WCS"
    assert not post("wms", "/api/update_location", {"pallet_id": p0, "to": items[1]["Location"]})["ok"], "ô đang có pallet khác"
    # sửa tay ô: khoá ô trống, không khoá ô có pallet; làm trống ô có pallet -> pallet mất vị trí
    assert post("wms", "/api/locations/set", {"code": free, "state": "LOCKED"})["ok"]
    assert not post("wms", "/api/locations/set", {"code": free2, "state": "LOCKED"})["ok"]
    assert post("wms", "/api/locations/set", {"code": free, "state": "EMPTY"})["ok"]
    # pallet tay: tạo, sửa trạng thái, xoá (nhả ô)
    free3 = next(c["code"] for c in get("wms", "/api/locations?wh=C") if c["state"] == "EMPTY" and not c.get("task"))
    pc = post("wms", "/api/pallets/create", {"item_code": "TEST_5", "ctn": 4, "location": free3})
    assert pc["ok"] and get("wms", "/api/pallets/" + pc["pallet_id"])["status"] == "STORED"
    assert post("wms", "/api/pallets/update", {"pallet_id": pc["pallet_id"], "status": "IDENTIFIED"})["ok"]
    assert post("wms", "/api/pallets/delete", {"pallet_id": pc["pallet_id"]})["ok"]
    assert loc("wms", free3)["state"] == "EMPTY"
    # xuất hàng từ pallet đã định danh
    do = post("wms", "/api/export", {"belt": "BCXB001", "picks": [{"pallet_id": p0, "ctn_need": 3}]})
    assert do["ok"] and do["body"]["Item_Information"][0]["Ctn_Need"] == 3
    until(lambda: get("wms", "/api/pallets/" + p0)["ctn_shipped"] == 3 if "ctn_shipped" in get("wms", "/api/pallets/" + p0)
          else len(get("wms", "/api/pallets/" + p0).get("shipped", [])) == 3, timeout=30, what="xuất 3 thùng")
    inv = {(r["wh"], r["item_code"]): r for r in get("wms", "/api/inventory")}
    assert inv[("C", "TEST_5")]["cartons"] == 4, inv   # 5 + 2 - 3 đã xuất
    assert not post("wms", "/api/products/delete", {"item_code": "TEST_5"})["ok"], "còn tồn thì không xoá sản phẩm"



@scenario
def s21_web_site():
    """Site chung: trang chính, logo, chuyển tiếp API WMS / WCS (GET, POST, file Excel), app báo lỗi khi tắt,
    trang "/" của 2 app chuyển về site, nút hướng dẫn mở PDF."""
    w = U["web"]
    r = http.get(w + "/")
    assert r.status_code == 200 and "/static/wms.js" in r.text and "/huong-dan" in r.text and "logo.png" in r.text
    assert http.get(w + "/static/logo.png").status_code == 200
    assert http.get(w + "/wms/api/config").json()["version"] and http.get(w + "/wcs/api/status").json()["version"]
    r = http.post(w + "/wms/api/products", json={"item_code": "WEB_1", "item_name": "qua site", "inner_carton": 1, "inner_pallet": 1})
    assert r.json()["ok"]
    assert any(p["item_code"] == "WEB_1" for p in http.get(w + "/wms/api/products").json())
    x = http.get(w + "/wms/api/locations/export?fmt=raw")
    assert x.status_code == 200 and x.content[:2] == b"PK" and "RAW" in x.headers["content-disposition"]
    assert http.get(w + "/khong-co/api").status_code == 404
    r = http.get(U["wms"] + "/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].endswith(f":{P['web']}/"), r.headers
    assert http.get(U["wcs"] + "/", follow_redirects=False).status_code == 307
    h = http.get(w + "/huong-dan")
    pdf = os.path.join(ROOT, "docs", "HUONG_DAN_SU_DUNG.pdf")
    if os.path.exists(pdf):
        assert h.status_code == 200 and h.content[:4] == b"%PDF" and h.headers["content-type"] == "application/pdf"
    else:
        assert h.status_code == 404
    stop("wcs")
    r = http.get(w + "/wcs/api/status")
    assert r.status_code == 502 and "Không kết nối" in r.json()["msg"]
    start("wcs")


@scenario
def s22_tkelog_dot_location_and_latent_robot():
    # TKELog (WMS gốc) dùng dấu "." – WCS B tự đổi sang "-" khi nhận
    body = {"Task_ID": "1/1_MovePallet_dot", "WH_Code": "WH_TB", "Pallet_ID": "LPN-DOT", "Type_Robot_ID": 2,
            "Type_Move_ID": 11, "Location_Old": "BDD09", "Location_New": "S02.251.2"}
    assert post("wcs", "/move-service/task/wms/move_pallet", body)["code"] == 200
    until(lambda: any(m["task_id"] == "1/1_MovePallet_dot" and m["status"] == "SUCCEEDED" for m in get("wcs", "/api/moves")),
          what="task dấu chấm chạy xong")
    m = next(m for m in get("wcs", "/api/moves") if m["task_id"] == "1/1_MovePallet_dot")
    assert m["loc_new"] == "S02-251-2", m
    assert loc("wcs", "S02-251-2")["state"] == "FULL"
    # kho S tự tạo khi nạp file: robot mặc định đội gầm (2); kho khác nâng hạ (1)
    with tempfile.TemporaryDirectory() as d:
        wh = L.Warehouses(os.path.join(d, "wh.json"))
        wh.ensure_for(["S01-1-1", "S02-1-1", "S-5-1", "C24-1-1", "A1-1-1"])
        got = {w["code"]: w["robot"] for w in wh.all()}
    assert got == {"S": 2, "S02": 2, "C": 1, "A": 1}, got


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    for db in (DB_WCS, DB_WMS):
        MongoClient(MONGO).drop_database(db)
    write_cfg()
    try:
        for n in ("rcs", "ids", "scs", "wms", "wcs", "web"):
            start(n)
        for fn in (s01_move_group_success, s02_move_fail_skip_then_retry, s03_unknown_location_and_bad_robot,
                   s04_duplicate_task_id, s22_tkelog_dot_location_and_latent_robot, s05_identify_po_full_flow, s06_identify_asn, s07_identify_cancel,
                   s08_export_do, s09_wms_outage_retry, s10_line_not_owned_and_cutover,
                   s11_foreign_rcs_callback_ignored, s12_restart_wcs_mid_task_polling_only,
                   s13_restart_wms_outbox_survives, s14_dest_busy_waits, s15_manual_move_and_raw_order,
                   s16_config_on_web, s17_dashboards, s18_highbay_locations, s19_import_raw_and_cascade,
                   s20_wms_catalog_and_manual, s21_web_site):
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
