"""Test WCS B với phần mềm trạm G8 THẬT chạy ở chế độ mô phỏng (không cần phần cứng).

  G8-Export: app thật, PLC + camera driver = simulator; thùng đi qua bằng tools.simulator box.
  G8-ID:     app thật ở MANUAL + công cụ mô phỏng của G8-ID (công nhân ảo tự đặt thùng, tự bấm ĐỊNH DANH).
             Ở MANUAL G8-ID gửi pallet_identify_done về công cụ mô phỏng (thiết kế của G8-ID), nên test
             chuyển tiếp các bản tin đó sang WCS B, đúng body trạm gửi.
  2 bàn:     2 bản G8-ID thật chạy song song (tools/g8id_desks.py), lệnh sản xuất nạp bằng file định dạng G8,
             người vận hành bấm Định danh cả 2 lệnh, theo dõi tiến độ tới Hoàn thành, robot cất pallet.

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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import e2e_test as E  # noqa: E402
import g8id_desks as G  # noqa: E402

http = httpx.Client(timeout=15, trust_env=False)
EXP_UI = "http://127.0.0.1:8080"
EXP_SCS_PORT = 18700
ID_ORDER, ID_UI, ID_SIM = "http://127.0.0.1:8500", "http://127.0.0.1:8600", "http://127.0.0.1:8700"
extra: list[subprocess.Popen] = []
extra_desks: list = []


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
    MongoClient(E.MONGO).drop_database("e2e_G8_EXPORT")
    st = os.path.join(E.TMP, "g8export_settings.json")
    json.dump({"mongo_uri": E.MONGO, "mongo_db": "e2e_G8_EXPORT", "ui_port": 8080, "open_browser": False, "data_dir": os.path.join(E.TMP, "g8x")},
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
    cfg["mongo"].update(uri=E.MONGO, start_bundled=False, db="e2e_g8id", db_manual="e2e_g8id_manual")
    cfg["pallet_done_trigger"] = "auto"
    json.dump(cfg, open(os.path.join(run, "config.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for db in ("e2e_g8id", "e2e_g8id_manual"):
        MongoClient(E.MONGO).drop_database(db)
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

    E.post("wms", "/api/products", {"item_code": "TEST_2", "item_name": "SP test 2 thùng/pallet", "inner_carton": 6,
                                    "inner_pallet": 2, "gw": 1.637})
    r = E.idf("PO", 4, item="TEST_2", line="MCSX010")
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


# ---------------------------------------------------------------------- 2 bàn định danh G8-ID + lệnh sản xuất
@E.scenario
def m03_two_g8id_desks_production_orders():
    # bàn 2, 3 (cổng 8501/8601/8701, 8502/8602/8702) - bàn 1 dùng cho m02
    desks = [G.Desk(2, "MCSX001", ARGS.id, os.path.join(E.TMP, "g8id_desks"), E.MONGO, E.PY, E.LOGDIR, "e2e_g8id"),
             G.Desk(3, "MCSX005", ARGS.id, os.path.join(E.TMP, "g8id_desks"), E.MONGO, E.PY, E.LOGDIR, "e2e_g8id")]
    extra_desks.extend(desks)
    for d in desks:
        d.prepare()
        d.start()
    done = G.register(E.U["wcs"], E.U["wms"], [(d.line_id, d.order_url, pt) for d, pt in zip(desks, ("BDD01", "BDD05"))])
    assert len(done) == 4, done
    assert E.post("wms", "/api/products", {"item_code": "TEST_D1", "item_name": "SP bàn 1 (6c/T)", "inner_carton": 6,
                                           "inner_pallet": 3, "gw": 1.637, "lines": "MCSX001(20W)"})["ok"]
    assert E.post("wms", "/api/products", {"item_code": "TEST_D2", "item_name": "SP bàn 2 (12c/T)", "inner_carton": 12,
                                           "inner_pallet": 2, "gw": 2.1, "lines": "MCSX005(40W)"})["ok"]
    g1 = [None] * 17
    g1[7], g1[11] = "Thành phẩm", "Nguyên phụ liệu"
    rows = [g1, [t for _, t in E.F.ORDER_COLS],
            ["VAS-D-1", "KTM001RB", "KVT002", "26/11/2026", "MCSX001(20W)", "Kế hoạch", "Thành Phẩm", 6, 36, 0, 36, 0, 0, 0, 0, "TEST_D1", "bàn 1"],
            ["VAS-D-2", "KTM001RB", "KVT002", "26/11/2026", "MCSX005(40W)", "Đang sản xuất", "Thành Phẩm", 4, 48, 0, 48, 0, 0, 0, 0, "TEST_D2", "bàn 2"]]
    pv = E.upload("/api/prod/import/preview", "CKHC_G8_San_Xuat_2ban.xlsx", E.xlsx_bytes(rows))
    assert pv["ok"], pv
    assert E.post("wms", "/api/prod/import/apply", {"token": pv["token"], "statuses": ["Kế hoạch", "Đang sản xuất"]})["ok"]
    orders = {o["code"]: o for o in E.get("wms", "/api/prod?q=VAS-D-")}
    for code in ("VAS-D-1", "VAS-D-2"):   # người vận hành bấm Định danh trên từng lệnh (bàn theo Line của lệnh)
        r = E.post("wms", "/api/prod/start", {"key": orders[code]["key"]})
        assert r["ok"] and r["pallets"] == 2, r
    both = [o["status"] for o in E.get("wms", "/api/prod?q=VAS-D-")]
    assert both == ["IDENTIFYING", "IDENTIFYING"], both
    seen: dict[str, list] = {"VAS-D-1": [], "VAS-D-2": []}

    def step():
        for d in desks:
            d.pump(E.U["wcs"])
        cur = {o["code"]: o for o in E.get("wms", "/api/prod?q=VAS-D-")}
        for code, o in cur.items():
            seen[code].append((o["status"], o["progress"].get("pallets_done", 0), o["progress"].get("ctn_done", 0)))
        return all(o["status"] == "DONE" for o in cur.values())
    E.until(step, timeout=300, every=1, what="2 bàn G8-ID định danh xong 2 lệnh sản xuất")
    for code, ctn in (("VAS-D-1", 6), ("VAS-D-2", 4)):
        s_ = seen[code]
        assert s_[-1] == ("DONE", 2, ctn), (code, s_[-1])
        assert any(x[0] == "IDENTIFYING" and x[1] == 1 for x in s_), f"{code}: phải thấy tiến độ 1/2 pallet giữa chừng"
        assert [x[2] for x in s_] == sorted(x[2] for x in s_), f"{code}: tiến độ phải tăng dần"
    # mỗi bàn chỉ nhận lệnh của mình
    out = [m for m in E.get("wcs", "/api/messages?q=orderTask&limit=100") if m["dir"] == "OUT"]
    for d in desks:
        mine = [m for m in out if str(d.order_port) in m.get("peer", "") + m.get("path", "")]
        assert mine, f"bàn {d.line_id} phải nhận orderTask"
    E.until(lambda: all(o["progress"]["pallets_stored"] == 2 for o in E.get("wms", "/api/prod?q=VAS-D-")),
            timeout=90, every=1, what="robot cất 4 pallet của 2 bàn vào kho")


def main():
    global ARGS
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", default="")
    ap.add_argument("--id", default="")
    ARGS = ap.parse_args()
    for db in (E.DB_WCS, E.DB_WMS):
        MongoClient(E.MONGO).drop_database(db)
    E.write_cfg()
    add_lines()
    try:
        for n in ("rcs", "ids", "scs", "wms", "wcs"):
            E.start(n)
        if ARGS.export:
            m01_g8_export_real_app()
        if ARGS.id:
            m02_g8_id_real_app_manual()
            m03_two_g8id_desks_production_orders()
    finally:
        for p in extra:
            p.kill()
        for d in extra_desks:
            d.stop()
        for n in list(E.procs):
            E.stop(n)
    ok = sum(1 for r in E.results if r[1])
    print(f"\n==== {ok}/{len(E.results)} PASS ====")
    for name, good, info in E.results:
        print(f"{'PASS' if good else 'FAIL'}  {name}  {info}")
    sys.exit(0 if ok == len(E.results) else 1)


if __name__ == "__main__":
    main()
