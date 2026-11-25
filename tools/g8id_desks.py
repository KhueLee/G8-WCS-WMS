"""Chạy nhiều bàn định danh G8-ID THẬT ở chế độ mô phỏng, phối hợp với WMS ảo + WCS B.

Mỗi bàn = một bản chép thư mục G8-ID-Deploy (không sửa phần mềm trạm), chạy ở MANUAL kèm công cụ mô phỏng của G8-ID
(công nhân ảo đặt thùng lên cân, máy in / đầu đọc giả, tự bấm ĐỊNH DANH khi đủ pallet). Cổng riêng cho từng bàn:

  bàn n:  nhận lệnh 8500+n-1 (WCS gửi /ids/orderTask)   giao diện 8600+n-1   công cụ mô phỏng 8700+n-1

Ở MANUAL, G8-ID gửi pallet_identify_done về công cụ mô phỏng (thiết kế của G8-ID); công cụ này làm "cầu nối"
chuyển tiếp nguyên văn sang WCS B, như bàn thật gửi thẳng khi chạy AUTO.

Chạy demo (cần run.bat demo đang chạy, MongoDB 27018):
  .venv\\Scripts\\python tools\\g8id_desks.py --g8id ..\\G8-ID\\G8-ID-Deploy --desks MCSX001:BDD01,MCSX005:BDD05 --register
  --register: thêm 2 bàn vào cấu hình WCS B (identify_lines) và WMS ảo (Line -> điểm sàn) nếu chưa có.
G8-ID cần thư viện riêng: .venv\\Scripts\\python -m pip install -r <G8-ID-Deploy>\\requirements.txt
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

import httpx
from pymongo import MongoClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
http = httpx.Client(timeout=15, trust_env=False)


def wait_http(url: str, timeout: float = 60) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if http.get(url).status_code < 500:
                return
        except Exception:  # noqa: BLE001 - chưa lên
            pass
        time.sleep(0.5)
    raise RuntimeError(f"không lên: {url}")


class Desk:
    """Một bàn định danh G8-ID (bản chép riêng) ở MANUAL + công cụ mô phỏng."""

    def __init__(self, n: int, line_id: str, src: str, workdir: str, mongo: str, py: str = sys.executable,
                 log_dir: str = "", db_prefix: str = "g8id"):
        self.n, self.line_id, self.src, self.py = n, line_id, src, py
        self.dir = os.path.join(workdir, f"desk{n}_{line_id}")
        self.mongo, self.db = mongo, f"{db_prefix}_desk{n}"
        self.order_port, self.ui_port, self.sim_port = 8500 + n - 1, 8600 + n - 1, 8700 + n - 1
        self.ui, self.sim = f"http://127.0.0.1:{self.ui_port}", f"http://127.0.0.1:{self.sim_port}"
        self.order_url = f"http://127.0.0.1:{self.order_port}/ids"
        self.log_dir = log_dir or os.path.join(workdir, "logs")
        self.procs: list[subprocess.Popen] = []
        self.forwarded: set[str] = set()
        self.weight = 0

    # ------------------------------------------------------------------ chuẩn bị bản chép
    def prepare(self, fresh_db: bool = True) -> None:
        if os.path.exists(self.dir):
            shutil.rmtree(self.dir)
        shutil.copytree(self.src, self.dir, ignore=shutil.ignore_patterns("data", "logs", "wheels", "drivers", "mongodb",
                                                                           ".venv", "__pycache__"))
        os.makedirs(os.path.join(self.dir, "data"), exist_ok=True)
        with open(os.path.join(self.dir, "data", "mode.json"), "w", encoding="utf-8") as f:
            json.dump({"mode": "MANUAL"}, f)
        p = os.path.join(self.dir, "config.json")
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["station_id"] = f"ST{self.n:02d}"
        cfg["desk_no"] = self.n
        cfg["ui"].update(port=self.ui_port, open_browser=False)
        cfg["order_server"]["port"] = self.order_port
        cfg["simulator"]["port"] = self.sim_port
        cfg["mongo"].update(uri=self.mongo, start_bundled=False, db=self.db, db_manual=self.db + "_manual")
        cfg["pallet_done_trigger"] = "auto"
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        if fresh_db:
            for db in (self.db, self.db + "_manual"):
                MongoClient(self.mongo).drop_database(db)

    def _spawn(self, args: list[str], name: str) -> subprocess.Popen:
        # urllib của G8-ID đi qua HTTP_PROXY của máy nếu không có NO_PROXY
        env = dict(os.environ, PYTHONUTF8="1", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
                   G8ID_NO_BROWSER="1")
        os.makedirs(self.log_dir, exist_ok=True)
        out = open(os.path.join(self.log_dir, f"desk{self.n}_{name}.log"), "a", encoding="utf-8")
        p = subprocess.Popen([self.py] + args, cwd=self.dir, env=env, stdout=out, stderr=subprocess.STDOUT)
        self.procs.append(p)
        return p

    # ------------------------------------------------------------------ chạy
    def start(self, carton_weight: int = 1637) -> None:
        self._spawn(["-m", "app.main"], "app")
        wait_http(self.ui + "/api/mode")
        mode = http.get(self.ui + "/api/mode").json()
        if "MANUAL" not in json.dumps(mode):
            raise RuntimeError(f"G8-ID bàn {self.n} không ở MANUAL: {mode}")
        for _ in range(6):  # công cụ mô phỏng tự thoát nếu app chính chưa trả lời kịp -> thử lại
            sp = self._spawn(["simulator/sim_main.py"], "sim")
            try:
                wait_http(self.sim + "/sim/state", timeout=15)
                break
            except RuntimeError:
                sp.kill()
        else:
            raise RuntimeError(f"công cụ mô phỏng G8-ID bàn {self.n} không lên")
        # công nhân ảo: tự đặt thùng, tự bấm ĐỊNH DANH khi đủ pallet
        http.post(self.sim + "/sim/set", json={"auto": True, "auto_identify": True, "carton_weight": carton_weight})
        self.weight = carton_weight

    def pump(self, wcs_url: str) -> int:
        """Một nhịp cầu nối: bấm Bắt đầu nếu trạm dừng mà có lệnh; thùng giả nặng đúng khối lượng chuẩn bàn đang chờ
        (GW sản phẩm, vd. "2100 ± 100 GAM"); chuyển pallet_identify_done mới sang WCS B."""
        st = http.get(self.ui + "/api/state").json()
        if not st.get("running") and st.get("rows"):
            http.post(self.ui + "/api/start", json={})   # như công nhân bấm Bắt đầu
        m = re.match(r"\s*(\d+)", str((st.get("pallet") or {}).get("weight") or ""))
        if m and int(m.group(1)) > 0 and int(m.group(1)) != self.weight:
            self.weight = int(m.group(1))
            http.post(self.sim + "/sim/set", json={"carton_weight": self.weight})
        n = 0
        for rec in http.get(self.sim + "/sim/state").json().get("received", []):
            b = rec["body"]
            k = f"{b.get('Task_ID')}|{b.get('palletID')}"
            if k in self.forwarded:
                continue
            r = http.post(wcs_url.rstrip("/") + "/identify-service/ids/pallet_identify_done", json=b)
            if r.status_code < 300 and r.json().get("code") == 1000:
                self.forwarded.add(k)
                n += 1
        return n

    def state(self) -> dict:
        return http.get(self.ui + "/api/state").json()

    def stop(self) -> None:
        for p in self.procs:
            p.kill()
        self.procs.clear()


def register(wcs: str, wms: str, desks: list[tuple[str, str, str]]) -> list[str]:
    """Thêm bàn vào cấu hình WCS B (identify_lines) và WMS ảo (stations) nếu chưa có. desks = [(line, url, điểm sàn)]."""
    done = []
    s = http.get(wcs + "/api/config").json()["config"]
    have = {x["line_id"] for x in s["identify_lines"]}
    for line, url, point in desks:
        if line not in have:
            s["identify_lines"].append({"line_id": line, "url": url, "location": point, "enabled": True,
                                        "name": f"G8-ID mô phỏng {line}"})
            done.append(f"WCS: thêm {line} -> {url} (điểm {point})")
        else:
            for x in s["identify_lines"]:
                if x["line_id"] == line and (x["url"] != url or x.get("location") != point):
                    x.update(url=url, location=point)
                    done.append(f"WCS: sửa {line} -> {url} (điểm {point})")
    r = http.put(wcs + "/api/config", json=s).json()
    if not r.get("ok"):
        raise RuntimeError(f"WCS không lưu cấu hình: {r}")
    for line, _, _ in desks:   # line có thể đang tắt (tab Trạm / Line)
        http.post(wcs + "/api/lines/enable", json={"line_id": line, "enabled": True})
    m = http.get(wms + "/api/settings").json()["config"]
    for line, _, point in desks:
        if m["stations"].get(line) != point:
            m["stations"][line] = point
            done.append(f"WMS: Line {line} -> điểm sàn {point}")
    r = http.put(wms + "/api/settings", json=m).json()
    if not r.get("ok"):
        raise RuntimeError(f"WMS không lưu cấu hình: {r}")
    return done


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Chạy nhiều bàn định danh G8-ID thật ở chế độ mô phỏng, nối với WCS B")
    ap.add_argument("--g8id", default=os.path.join(os.path.dirname(ROOT), "G8-ID", "G8-ID-Deploy"),
                    help="thư mục G8-ID-Deploy (mặc định ..\\G8-ID\\G8-ID-Deploy)")
    ap.add_argument("--desks", default="MCSX001:BDD01,MCSX005:BDD05", help="LINE:điểm_sàn,... (mặc định 2 bàn)")
    ap.add_argument("--wcs", default="http://127.0.0.1:9000")
    ap.add_argument("--wms", default="http://127.0.0.1:9100")
    ap.add_argument("--mongo", default="mongodb://127.0.0.1:27018")
    ap.add_argument("--register", action="store_true", help="thêm các bàn vào cấu hình WCS B / WMS ảo nếu chưa có")
    a = ap.parse_args()
    if not os.path.isfile(os.path.join(a.g8id, "app", "main.py")):
        sys.exit(f"Không thấy G8-ID ở {a.g8id} (cần thư mục G8-ID-Deploy)")
    try:
        import flask, waitress  # noqa: F401,E401 - thư viện của G8-ID
    except ImportError:
        sys.exit(f"Thiếu thư viện của G8-ID: {sys.executable} -m pip install -r {os.path.join(a.g8id, 'requirements.txt')}")
    pairs = [x.split(":") for x in a.desks.split(",") if x.strip()]
    work = os.path.join(ROOT, "data", "g8id_desks")
    desks = [Desk(i + 1, ln, a.g8id, work, a.mongo) for i, (ln, _) in enumerate(pairs)]
    if a.register:
        for t in register(a.wcs, a.wms, [(d.line_id, d.order_url, pt) for d, (_, pt) in zip(desks, pairs)]):
            print("  " + t)
    try:
        for d in desks:
            print(f"Bàn {d.n} ({d.line_id}): chuẩn bị {d.dir}")
            d.prepare()
            d.start()
            print(f"  G8-ID {d.ui}  (mật khẩu thông số trong config.json) · mô phỏng {d.sim} · nhận lệnh {d.order_url}")
        print("Đang chạy cầu nối pallet_identify_done -> WCS B. Ctrl+C để dừng.")
        while True:
            for d in desks:
                try:
                    n = d.pump(a.wcs)
                    if n:
                        print(f"  {time.strftime('%H:%M:%S')} bàn {d.n} ({d.line_id}): chuyển {n} pallet xong sang WCS B")
                except Exception as e:  # noqa: BLE001 - G8-ID / WCS tạm không trả lời: thử lại nhịp sau
                    print(f"  bàn {d.n}: {e}")
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for d in desks:
            d.stop()


if __name__ == "__main__":
    main()
