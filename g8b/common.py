"""Phần dùng chung cho WMS ảo, WCS B và các simulator: cấu hình, MongoDB, log, HTTP."""
from __future__ import annotations

import copy
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import TimedRotatingFileHandler
from typing import Any

import httpx
from pymongo import MongoClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TZ = timezone(timedelta(hours=7))


def now() -> datetime:
    return datetime.now()


def ts() -> str:
    return now().strftime("%Y-%m-%d__%H:%M:%S.%f")


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(name: str, defaults: dict) -> dict:
    """config/<name>.json đè lên mặc định. Biến môi trường G8B_<NAME>_CONFIG trỏ tới file khác (dùng khi test)."""
    path = os.environ.get(f"G8B_{name.upper()}_CONFIG") or os.path.join(ROOT, "config", f"{name}.json")
    cfg = defaults
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            cfg = deep_merge(defaults, json.load(f))
    cfg["_path"] = path
    return cfg


def site_port() -> int:
    """Cổng site giao diện chung (config/web.json, mặc định 5510) – trang \"/\" của WMS / WCS chuyển về đây."""
    return int(load_config("web", {"server": {"port": 5510}})["server"]["port"])


def setup_logging(name: str, log_dir: str | None = None, keep_days: int = 30) -> logging.Logger:
    log_dir = log_dir or os.path.join(ROOT, "logs", name)
    os.makedirs(log_dir, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s [%(name)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = TimedRotatingFileHandler(os.path.join(log_dir, f"{name}.log"), when="midnight",
                                  backupCount=keep_days, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    return logging.getLogger(name)


def mongo_db(uri: str, db: str):
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=False)
    client.admin.command("ping")
    return client[db]


def clean(doc: Any) -> Any:
    """Bỏ _id / đổi datetime sang chuỗi để trả JSON."""
    if isinstance(doc, list):
        return [clean(d) for d in doc]
    if isinstance(doc, dict):
        return {k: clean(v) for k, v in doc.items() if k != "_id"}
    if isinstance(doc, datetime):
        return doc.strftime("%Y-%m-%d %H:%M:%S")
    return doc


class MsgLog:
    """Ghi mọi bản tin vào/ra vào collection `messages` (TTL) để xem trên giao diện và đối soát."""

    def __init__(self, db, keep_days: int = 14):
        self.coll = db["messages"]
        self.coll.create_index("t", expireAfterSeconds=keep_days * 86400)
        self.coll.create_index([("t", -1)])
        self.log = logging.getLogger("msg")

    def add(self, direction: str, peer: str, path: str, body: Any, status: Any = None, resp: Any = None,
            ms: float | None = None):
        doc = {"t": now(), "dir": direction, "peer": peer, "path": path,
               "body": _short(body), "status": status, "resp": _short(resp), "ms": ms}
        try:
            self.coll.insert_one(doc)
        except Exception:
            self.log.exception("ghi messages lỗi")
        self.log.info("%s %s %s status=%s body=%s resp=%s", direction, peer, path, status,
                      json.dumps(body, ensure_ascii=False, default=str)[:2000],
                      json.dumps(resp, ensure_ascii=False, default=str)[:1000])


def _short(v: Any, n: int = 20000) -> Any:
    s = json.dumps(v, ensure_ascii=False, default=str)
    if len(s) <= n:
        return v
    return {"_truncated": s[:n]}


_client_lock = threading.Lock()
_clients: dict[float, httpx.Client] = {}


def http_post(url: str, body: Any, timeout: float = 10.0, msglog: MsgLog | None = None,
              peer: str = "", headers: dict | None = None) -> tuple[int, Any, str]:
    """POST JSON UTF-8 (ensure_ascii=False như WCS gốc). Trả (http_status, json|text, lỗi)."""
    with _client_lock:
        c = _clients.get(timeout)
        if c is None:
            c = _clients[timeout] = httpx.Client(timeout=timeout, trust_env=False)
    t0 = time.time()
    data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
    h = {"Content-Type": "application/json; charset=utf-8"}
    h.update(headers or {})
    try:
        r = c.post(url, content=data, headers=h)
        try:
            resp: Any = r.json()
        except Exception:
            resp = r.text[:2000]
        status, err = r.status_code, ""
    except Exception as e:
        status, resp, err = 0, None, f"{type(e).__name__}: {e}"
    if msglog:
        msglog.add("OUT", peer, url, body, status or err, resp, round((time.time() - t0) * 1000, 1))
    return status, resp, err


def ci_get(d: dict, *keys: str, default: Any = None) -> Any:
    """Lấy trường không phân biệt hoa thường, thử lần lượt nhiều tên."""
    if not isinstance(d, dict):
        return default
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:
        if k.lower() in low and low[k.lower()] is not None:
            return low[k.lower()]
    return default


def save_config(cfg: dict) -> str:
    """Ghi cấu hình ra file của nó (giữ bản .bak trước đó). Trả đường dẫn."""
    path = cfg["_path"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {k: v for k, v in cfg.items() if not k.startswith("_")}
    if os.path.exists(path):
        try:
            with open(path, "rb") as src, open(path + ".bak", "wb") as dst:
                dst.write(src.read())
        except OSError:
            pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def today_start() -> datetime:
    return now().replace(hour=0, minute=0, second=0, microsecond=0)


def hourly(coll, field: str, match: dict) -> list[int]:
    """Số bản ghi theo giờ (0-23) trong ngày hôm nay, theo trường thời gian `field`."""
    q = dict(match)
    q[field] = {"$gte": today_start()}
    out = [0] * 24
    for r in coll.aggregate([{"$match": q}, {"$group": {"_id": {"$hour": f"${field}"}, "n": {"$sum": 1}}}]):
        out[int(r["_id"])] = r["n"]
    return out
