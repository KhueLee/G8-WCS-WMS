"""Tiện ích cho 3 file install.bat / run.bat / update.bat (chỉ dùng thư viện chuẩn, trừ lệnh check).

  python -m g8b.ctl ports      in "MONGO WMS WCS WEB" (cổng lấy từ config\\*.json)
  python -m g8b.ctl up PORT    mã thoát 0 nếu cổng đang mở trên máy này
  python -m g8b.ctl status     bảng trạng thái các thành phần
  python -m g8b.ctl running    mã thoát 0 nếu còn tiến trình luồng B đang chạy
  python -m g8b.ctl stop       dừng cửa sổ run.bat, WMS / WCS / site / simulator, MongoDB của thư mục này
  python -m g8b.ctl firewall   mở cổng WMS / WCS / site (cần quyền Administrator)
  python -m g8b.ctl check      kiểm tra môi trường; mã thoát 1 nếu có LỖI
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config")
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _json(name: str) -> dict:
    try:
        with open(os.path.join(CFG, name + ".json"), encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def ports() -> dict:
    wms, wcs, web = _json("wms"), _json("wcs"), _json("web")
    uri = (wcs.get("mongo") or {}).get("uri") or (wms.get("mongo") or {}).get("uri") or "mongodb://127.0.0.1:27018"
    return {"mongo": urlparse(uri).port or 27017,
            "wms": (wms.get("server") or {}).get("port", 9100),
            "wcs": (wcs.get("server") or {}).get("port", 9000),
            "web": (web.get("server") or {}).get("port", 5510)}


def is_up(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


class ProcListError(RuntimeError):
    pass


def procs() -> list[dict]:
    """Tiến trình cmd / python / mongod kèm dòng lệnh (qua PowerShell CIM). Không đọc được thì ném ProcListError."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='cmd.exe' or Name='mongod.exe'\" | "
          "Select-Object ProcessId,Name,CommandLine,ExecutablePath | ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                           timeout=60, creationflags=NOWIN)
        out = r.stdout.strip()
        if r.returncode != 0 and not out:
            raise ProcListError(r.stderr.strip()[:200] or f"powershell mã {r.returncode}")
        data = json.loads(out) if out else []
    except (OSError, ValueError, subprocess.SubprocessError) as ex:
        raise ProcListError(str(ex)) from ex
    return data if isinstance(data, list) else [data]


def ours() -> list[dict]:
    """Tiến trình của luồng B chạy từ thư mục này (không đụng MongoDB / python của phần mềm khác)."""
    root = ROOT.lower()
    res = []
    for p in procs():
        cl = (p.get("CommandLine") or "").lower()
        ex = (p.get("ExecutablePath") or "").lower()
        if p["ProcessId"] == os.getpid() or (root not in cl and not ex.startswith(root)):
            continue
        name = p["Name"].lower()
        if name == "cmd.exe" and re.search(r"run\.bat\"?\s+__(svc|mongo)", cl):
            res.append({**p, "kind": "window"})
        elif name == "python.exe" and re.search(r"-m\s+g8b\.(wms|wcs|web|sim)\b", cl):
            res.append({**p, "kind": "app"})
        elif name == "mongod.exe":
            res.append({**p, "kind": "mongo"})
    return res


def kill(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NOWIN)


def stop() -> int:
    try:
        ps = ours()
    except ProcListError as ex:
        print(f"  [LỖI] không đọc được danh sách tiến trình ({ex}) - chưa dừng gì")
        return 1
    # cửa sổ vòng lặp trước, để không tự chạy lại; rồi đến app; MongoDB tắt êm sau cùng
    for kind in ("window", "app"):
        for p in ps:
            if p["kind"] == kind:
                kill(p["ProcessId"])
                print(f"  dừng {'cửa sổ' if kind == 'window' else 'chương trình'} PID {p['ProcessId']}")
    mongo = [p for p in ps if p["kind"] == "mongo"]
    if mongo:
        port = ports()["mongo"]
        try:
            from pymongo import MongoClient
            MongoClient(f"mongodb://127.0.0.1:{port}", serverSelectionTimeoutMS=3000).admin.command("shutdown")
        except Exception:  # noqa: BLE001 - shutdown làm đứt kết nối nên luôn ném lỗi
            pass
        for _ in range(20):
            if not is_up(port):
                break
            time.sleep(0.5)
        for p in mongo:
            kill(p["ProcessId"])
        print(f"  dừng MongoDB cổng {port}")
    if not ps:
        print("  không có thành phần nào của thư mục này đang chạy")
    time.sleep(1)
    busy = [f"{k} {v}" for k, v in ports().items() if is_up(v)]
    if busy:
        print(f"  [CẢNH BÁO] cổng vẫn mở: {', '.join(busy)} - có thể do chương trình khác hoặc bản luồng B ở thư mục khác")
        return 1
    return 0


def status() -> None:
    p = ports()
    rows = [("MongoDB", p["mongo"]), ("WMS ảo", p["wms"]), ("WCS B", p["wcs"]), ("Site giao diện", p["web"])]
    for name, port in rows:
        print(f"  {name:<15} cổng {port:<6} {'ĐANG CHẠY' if is_up(port) else '-- tắt --'}")
    print(f"  Giao diện: http://{local_ip()}:{p['web']}  (trên máy này: http://127.0.0.1:{p['web']})")


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


RULES = (("WMS", "wms"), ("WCS", "wcs"), ("SITE", "web"))


def rule_name(label: str, port: int) -> str:
    return f"G8B {label} {port}"


def firewall() -> int:
    p, rc = ports(), 0
    for label, key in RULES:
        name = rule_name(label, p[key])
        subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"], capture_output=True)
        r = subprocess.run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={name}", "dir=in", "action=allow",
                            "protocol=TCP", f"localport={p[key]}"], capture_output=True)
        print(f"  [{'OK' if r.returncode == 0 else 'LỖI'}] mở cổng {p[key]} ({name})")
        rc |= r.returncode
    return 1 if rc else 0


# ---------------------------------------------------------------- check
class Report:
    def __init__(self):
        self.err = self.warn = 0

    def ok(self, msg):
        print(f"  [OK]        {msg}")

    def w(self, msg):
        self.warn += 1
        print(f"  [CẢNH BÁO]  {msg}")

    def e(self, msg):
        self.err += 1
        print(f"  [LỖI]       {msg}")


def mem_mb() -> tuple[int, int]:
    class MS(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                    ("avail", ctypes.c_ulonglong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                    ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ae", ctypes.c_ulonglong)]
    m = MS()
    m.len = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.total // 2**20, m.avail // 2**20


def check() -> int:
    r = Report()
    print("Python / thư viện")
    v = sys.version_info
    if v >= (3, 10) and struct.calcsize("P") == 8:
        r.ok(f"Python {v.major}.{v.minor}.{v.micro} 64-bit")
    else:
        r.e(f"Python {v.major}.{v.minor} {struct.calcsize('P') * 8}-bit: cần 3.10+ 64-bit")
    from importlib import metadata
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
        reqs = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    for req in reqs:
        name, _, want = req.partition("==")
        name = name.split("[")[0]
        try:
            have = metadata.version(name)
            (r.ok if not want or have == want else r.w)(f"{name} {have}" + ("" if not want or have == want else f" (cần {want})"))
        except metadata.PackageNotFoundError:
            r.e(f"thiếu thư viện {name}: chạy lại install.bat")

    print("Tệp chương trình / cấu hình")
    mongod = os.path.join(ROOT, "mongodb", "bin", "mongod.exe")
    (r.ok if os.path.isfile(mongod) else r.e)("mongodb\\bin\\mongod.exe" + ("" if os.path.isfile(mongod) else " không có"))
    for name in ("wms", "wcs", "web"):
        path = os.path.join(CFG, name + ".json")
        if not os.path.isfile(path):
            r.w(f"config\\{name}.json chưa có (dùng mặc định)")
            continue
        try:
            with open(path, encoding="utf-8-sig") as f:
                json.load(f)
            r.ok(f"config\\{name}.json đọc được")
        except ValueError as ex:
            r.e(f"config\\{name}.json sai định dạng JSON: {ex}")
    loc = os.path.join(ROOT, (_json("wcs").get("locations") or {}).get("file", "config/locations.xlsx"))
    (r.ok if os.path.isfile(loc) else r.w)(f"danh sách vị trí {os.path.relpath(loc, ROOT)}" + ("" if os.path.isfile(loc) else " chưa có"))

    print("Cổng mạng")
    p = ports()
    running = {}
    try:
        for pr in ours():
            running.setdefault(pr["kind"], []).append(pr)
    except ProcListError as ex:
        r.w(f"không đọc được danh sách tiến trình ({ex}): không phân biệt được cổng do luồng B hay chương trình khác dùng")
    for label, key in (("MongoDB", "mongo"), ("WMS ảo", "wms"), ("WCS B", "wcs"), ("Site", "web")):
        if not is_up(p[key]):
            r.ok(f"{label} cổng {p[key]} trống, sẵn sàng")
        elif (key == "mongo" and running.get("mongo")) or (key != "mongo" and running.get("app")):
            r.ok(f"{label} cổng {p[key]} đang do luồng B dùng (đang chạy)")
        else:
            r.w(f"{label} cổng {p[key]} đang bị chương trình khác chiếm: đổi cổng trong config hoặc tắt chương trình đó")
    if is_up(27017) and p["mongo"] != 27017:
        r.ok("MongoDB 27017 của WCS gốc / G8-ID đang chạy: không ảnh hưởng (B dùng cổng riêng)")

    print("Tường lửa (cho máy khác / RCS / trạm gọi vào)")
    for label, key in RULES:
        name = rule_name(label, p[key])
        found = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                               capture_output=True).returncode == 0
        (r.ok if found else r.w)(f"cổng {p[key]}: " + ("đã mở" if found else "chưa mở - chạy install.bat bằng Run as administrator"))

    print("Kết nối ra ngoài")
    rcs = (_json("wcs").get("rcs") or {}).get("url", "")
    u = urlparse(rcs)
    if u.hostname:
        ok = is_up(u.port or 80, u.hostname, timeout=2)
        (r.ok if ok else r.w)(f"RCS {rcs} " + ("kết nối được" if ok else "không kết nối được (bỏ qua nếu đang chạy mô phỏng)"))
    if shutil.which("git"):
        isgit = os.path.isdir(os.path.join(ROOT, ".git"))
        (r.ok if isgit else r.w)("Git có sẵn" + (", cập nhật được bằng update.bat" if isgit else ", nhưng thư mục không phải bản git clone: update.bat không dùng được"))
    else:
        r.w("chưa cài Git: update.bat không dùng được (https://git-scm.com/download/win)")

    print("Tài nguyên máy")
    total, avail = mem_mb()
    (r.ok if avail >= 1500 else r.w)(f"RAM trống {avail / 1024:.1f} / {total / 1024:.1f} GB (luồng B cần khoảng 1,5 GB)")
    free = shutil.disk_usage(ROOT).free / 2**30
    (r.ok if free >= 5 else r.w)(f"ổ đĩa còn trống {free:.0f} GB")

    print(f"\nKết quả: {r.err} lỗi, {r.warn} cảnh báo")
    return 1 if r.err else 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "ports":
        p = ports()
        print(p["mongo"], p["wms"], p["wcs"], p["web"])
    elif cmd == "up":
        return 0 if is_up(int(argv[1])) else 1
    elif cmd == "status":
        status()
    elif cmd == "running":
        try:
            return 0 if ours() else 1
        except ProcListError:
            return 0  # không biết chắc thì coi như đang chạy để update.bat dừng / chạy lại
    elif cmd == "stop":
        return stop()
    elif cmd == "firewall":
        return firewall()
    elif cmd == "check":
        return check()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
