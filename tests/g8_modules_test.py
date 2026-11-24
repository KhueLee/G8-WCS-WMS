"""Test WCS B với phần mềm trạm G8 THẬT chạy ở chế độ mô phỏng (không cần phần cứng).

  G8-Export: app thật, PLC + camera driver = simulator; thùng đi qua bằng tools.simulator box.
  G8-ID:     app thật ở MANUAL + công cụ mô phỏng của G8-ID (công nhân ảo tự đặt thùng, tự bấm ĐỊNH DANH).
             Ở MANUAL G8-ID gửi pallet_identify_done về công cụ mô phỏng (thiết kế của G8-ID), nên test
             chuyển tiếp các bản tin đó sang WCS B, đúng body trạm gửi.

Chạy: .venv\\Scripts\\python tests\\g8_modules_test.py --export <thư mục G8-Export> --id <thư mục G8-ID-Deploy>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

import httpx
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_test as E  # noqa: E402

http = httpx.Client(timeout=15, trust_env=False)
EXP_UI = "http://127.0.0.1:8080"
EXP_SCS_PORT = 18700
ID_ORDER, ID_UI, ID_SIM = "http://127.0.0.1:8500", "http://127.0.0.1:8600", "http://127.0.0.1:8700"
extra: list[subprocess.Popen] = []


def add_lines():
    p = os.path.join(E.TMP, "wcs.json")
    c = json.load(open(p, encoding="utf-8"))
    c["export_lines"].append({"line_id": "BCXB002", "url": f"http://127.0.0.1:{EXP_SCS_PORT}/scs", "location": "BCXB01"})
    c["identify_lines"].append({"line_id": "MCSX010", "url": ID_ORDER + "/ids", "location": "BDD09"})
    json.dump(c, open(p, "w", encoding="utf-8"))
    p = os.path.join(E.TMP, "wms.json")
    c = json.load(open(p, encoding="utf-8"))
    c["stations"]["MCSX010"] = "BDD09"
    json.dump(c, open(p, "w", encoding="utf-8"))


def spawn(cmd, cwd, env_extra, name):
    # máy dev có HTTP_PROXY: urllib của G8-ID sẽ gửi cả 127.0.0.1 qua proxy nếu không có NO_PROXY
    env = dict(os.environ, PYTHONUTF8="1", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
               G8ID_NO_BROWSER="1", **env_extra)
    out = open(os.path.join(E.LOGDIR, f"{name}.stdout.log"), "a", encoding="utf-8")
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT)
    extra.append(p)
    return p


def wait_http(url, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if http.get(url).status_code < 500:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"không lên: {url}")


# ---------------------------------------------------------------------- G8-Export
@E.scenario
def m01_g8_export_real_app():
    d = ARGS.export
    MongoClient().drop_database("e2e_G8_EXPORT")
    st = os.path.join(E.TMP, "g8export_settings.json")
    json.dump({"mongo_db": "e2e_G8_EXPORT", "ui_port": 8080, "open_browser": False, "data_dir": os.path.join(E.TMP, "g8x")},
              open(st, "w"))
    spawn([E.PY, "-m", "app.main"], d, {"G8_SETTINGS": st}, "g8export")
    wait_http(EXP_UI + "/api/config")
    cfg = http.get(EXP_UI + "/api/config").json()["config"]
    cfg["plc"]["driver"] = "simulator"
    for i, cam in enumerate(cfg["cameras"]):
        cam["driver"], cam["tcp_ip"] = "simulator", f"127.0.0.{i + 1}"
    cfg["tcp_barcode"].update(host="127.0.0.1", port=13000)
    cfg["wms"].update(bActive=True, UrlScs=f"http://127.0.0.1:{EXP_SCS_PORT}/",
                      UrlWms=E.U["wcs"] + "/export-service/scs/taskStatusReport", retry_seconds=2, tick_ms=500)
    cfg["ftp1"]["bActive"] = cfg["ftp2"]["bActive"] = False
    r = http.put(EXP_UI + "/api/config", json=cfg)
    assert r.status_code == 200, r.text
    time.sleep(2)
    r = http.post(EXP_UI + "/api/start").json()
    assert r.get("ok"), f"G8-Export không Start được: {r}"
    wait_http(f"http://127.0.0.1:{EXP_SCS_PORT}/scs/orderTask")

    def carton(p, i):
        return f"G8_EXP_ITEM|||||||6|0|EXP{p}{i:03d}||001||||||||01"
    body = {"DO_ID": 175550001, "WH_Code": "WH_TB", "Conveyor_Belt": "BCXB002", "Item_Information": [
        {"STT": 1, "Pallet_ID": "LPNX001", "Item_Code": "G8_EXP_ITEM", "Ctn": 3, "Ctn_Need": 2,
         "Carton_List": [carton("X001", i) for i in range(3)]},
        {"STT": 2, "Pallet_ID": "LPNX002", "Item_Code": "G8_EXP_ITEM", "Ctn": 2, "Ctn_Need": 2,
         "Carton_List": [carton("X002", i) for i in range(2)]}]}
    assert E.post("wcs", "/export-service/task/wms/export_items", body)["code"] == 200
    E.until(lambda: any(o["key"] == "scs-order:175550001" and o["status"] == "DONE" for o in E.get("wcs", "/api/outbox")),
            what="G8-Export nhận /scs/orderTask (code 1000)")
    for code in [carton("X001", 0), carton("X001", 1), carton("X002", 0), carton("X002", 1)]:
        subprocess.run([E.PY, "-m", "tools.simulator", "box", "--code", code, "--tcp-port", "13000", "--ftp-port", "0"],
                       cwd=d, check=True, capture_output=True, timeout=30)
        time.sleep(1.0)
    E.until(lambda: next(x for x in E.get("wcs", "/api/export") if x["do_id"] == 175550001)["status"] == "COMPLETED",
            timeout=60, what="G8-Export báo đủ 2 pallet về WCS B, WCS B báo OB_Pallet về WMS")
    rep = [m for m in E.get("wcs", "/api/messages?q=taskStatusReport&limit=50") if m["dir"] == "IN"]
    assert any(m["body"].get("Pallet_ID") == "LPNX001" and len(m["body"].get("Carton_List", [])) == 2 for m in rep), rep[:2]
    http.post(EXP_UI + "/api/stop")


# ---------------------------------------------------------------------- G8-ID
@E.scenario
def m02_g8_id_real_app_manual():
    d = ARGS.id
    run = os.path.join(E.TMP, "g8id")
    if os.path.exists(run):
        shutil.rmtree(run)
    shutil.copytree(d, run, ignore=shutil.ignore_patterns("data", "logs", "wheels", "drivers", "mongodb"))
    os.makedirs(os.path.join(run, "data"), exist_ok=True)
    json.dump({"mode": "MANUAL"}, open(os.path.join(run, "data", "mode.json"), "w"))
    cfg = json.load(open(os.path.join(run, "config.json"), encoding="utf-8"))
    cfg["ui"]["open_browser"] = False
    cfg["mongo"].update(start_bundled=False, db="e2e_g8id", db_manual="e2e_g8id_manual")
    cfg["pallet_done_trigger"] = "auto"
    json.dump(cfg, open(os.path.join(run, "config.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for db in ("e2e_g8id", "e2e_g8id_manual"):
        MongoClient().drop_database(db)
    spawn([E.PY, "-m", "app.main"], run, {}, "g8id")
    wait_http(ID_UI + "/api/mode")
    mode = http.get(ID_UI + "/api/mode").json()
    assert "MANUAL" in json.dumps(mode), f"G8-ID không ở MANUAL: {mode}"
    for _ in range(6):  # công cụ mô phỏng tự thoát nếu app chính chưa trả lời kịp -> thử lại
        sp = spawn([E.PY, "simulator/sim_main.py"], run, {}, "g8id_sim")
        try:
            wait_http(ID_SIM + "/sim/state", timeout=15)
            break
        except RuntimeError:
            sp.kill()
    else:
        raise RuntimeError("công cụ mô phỏng G8-ID không lên")
    http.post(ID_SIM + "/sim/set", json={"auto": True, "auto_identify": True, "carton_weight": 1637})
    time.sleep(5)  # chờ G8-ID nối các thiết bị giả (cân, đầu đọc, máy in) của công cụ mô phỏng

    r = E.post("wms", "/api/identify", {"kind": "PO", "line_id": "MCSX010", "pallets": 2, "cartons": 2, "gw": 1.637})
    po = r["po_id"]
    E.until(lambda: sum(1 for o in E.get("wcs", "/api/outbox") if o["key"].startswith("ids-order:") and str(po) in o["key"]
                        and o["status"] == "DONE") == 2, timeout=30, what="G8-ID nhận 2 orderTask (1000 SUCCESS)")
    forwarded: set[str] = set()

    def forward():
        st = http.get(ID_UI + "/api/state").json()
        if not st.get("running") and st.get("rows"):
            http.post(ID_UI + "/api/start", json={})  # như công nhân bấm Bắt đầu khi trạm đang dừng
        for rec in http.get(ID_SIM + "/sim/state").json().get("received", []):
            b = rec["body"]
            k = f"{b.get('Task_ID')}|{b.get('palletID')}"
            if k not in forwarded:
                rr = http.post(E.U["wcs"] + "/identify-service/ids/pallet_identify_done", json=b)
                assert rr.json()["code"] == 1000
                forwarded.add(k)
        o = next(o for o in E.get("wcs", "/api/identify") if o["po_id"] == po)
        return o["status"] == "COMPLETED"
    E.until(forward, timeout=120, every=1, what="G8-ID cân-in-đọc 2 pallet, WCS B báo Finished_Goods")
    ids_conf = [m for m in E.get("wcs", "/api/messages?q=confirm_product&limit=20") if m["dir"] == "OUT"]
    assert len([m for m in ids_conf if m["status"] == 200]) >= 2, "G8-ID phải nhận confirm_product"


def main():
    global ARGS
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", default="")
    ap.add_argument("--id", default="")
    ARGS = ap.parse_args()
    for db in (E.DB_WCS, E.DB_WMS):
        MongoClient().drop_database(db)
    E.write_cfg()
    add_lines()
    try:
        for n in ("rcs", "ids", "scs", "wms", "wcs"):
            E.start(n)
        if ARGS.export:
            m01_g8_export_real_app()
        if ARGS.id:
            m02_g8_id_real_app_manual()
    finally:
        for p in extra:
            p.kill()
        for n in list(E.procs):
            E.stop(n)
    ok = sum(1 for r in E.results if r[1])
    print(f"\n==== {ok}/{len(E.results)} PASS ====")
    for name, good, info in E.results:
        print(f"{'PASS' if good else 'FAIL'}  {name}  {info}")
    sys.exit(0 if ok == len(E.results) else 1)


if __name__ == "__main__":
    main()
