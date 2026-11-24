"""Outbox: hàng đợi bản tin gửi đi, lưu MongoDB, gửi lại tới khi bên nhận xác nhận.

Mỗi job có `key` duy nhất (idempotency key): tạo lại cùng key không sinh job mới.
Hàm `accept(status, resp)` của từng loại job quyết định thế nào là "bên nhận đã nhận".
Gửi xong (DONE) thì gọi `on_done(job, resp)`; quá `max_tries` thì DEAD (hiện trên giao diện, gửi lại tay được).
"""
from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Any, Callable

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from .common import MsgLog, http_post, now

log = logging.getLogger("outbox")

PENDING, SENDING, DONE, DEAD = "PENDING", "SENDING", "DONE", "DEAD"


class Outbox:
    def __init__(self, db, msglog: MsgLog, retry_s: float = 5.0, max_tries: int = 0, timeout_s: float = 10.0,
                 keep_days: int = 14):
        self.coll = db["outbox"]
        self.coll.create_index("key", unique=True)
        self.coll.create_index([("status", ASCENDING), ("next_at", ASCENDING)])
        self.coll.create_index("done_at", expireAfterSeconds=keep_days * 86400)
        self.msglog = msglog
        self.retry_s = retry_s
        self.max_tries = max_tries          # 0 = gửi lại mãi
        self.timeout_s = timeout_s
        self.kinds: dict[str, dict] = {}
        self._kick = threading.Event()
        self._stop = threading.Event()

    def register(self, kind: str, accept: Callable[[int, Any], tuple[bool, str]],
                 on_done: Callable[[dict, Any], None] | None = None,
                 build: Callable[[dict], tuple[str, Any, dict]] | None = None,
                 on_dead: Callable[[dict, Any, str], None] | None = None):
        """accept(status, resp) -> (ok, lý_do[, từ_chối_hẳn]).
        build(job) -> (url, body, headers): dựng lại lúc gửi (vd. gắn token mới)."""
        self.kinds[kind] = {"accept": accept, "on_done": on_done, "build": build, "on_dead": on_dead}

    def put(self, kind: str, key: str, url: str, body: Any, ref: dict | None = None, peer: str = "") -> bool:
        t = now()
        try:
            self.coll.insert_one({"key": key, "kind": kind, "url": url, "body": body, "ref": ref or {},
                                  "peer": peer, "status": PENDING, "tries": 0, "next_at": t,
                                  "created_at": t, "last_error": None})
        except DuplicateKeyError:
            log.info("outbox trùng key %s - bỏ qua", key)
            return False
        self._kick.set()
        return True

    def retry(self, key: str) -> bool:
        r = self.coll.update_one({"key": key, "status": {"$in": [DEAD, PENDING]}},
                                 {"$set": {"status": PENDING, "next_at": now(), "tries": 0}})
        self._kick.set()
        return r.modified_count == 1

    def start(self):
        # job đang SENDING lúc tắt -> gửi lại
        self.coll.update_many({"status": SENDING}, {"$set": {"status": PENDING, "next_at": now()}})
        threading.Thread(target=self._run, name="outbox", daemon=True).start()

    def stop(self):
        self._stop.set()
        self._kick.set()

    def _run(self):
        while not self._stop.is_set():
            self._kick.wait(0.5)
            self._kick.clear()
            try:
                while self._one():
                    pass
            except Exception:
                log.exception("outbox tick lỗi")

    def _one(self) -> bool:
        job = self.coll.find_one_and_update(
            {"status": PENDING, "next_at": {"$lte": now()}},
            {"$set": {"status": SENDING}, "$inc": {"tries": 1}},
            sort=[("next_at", ASCENDING)], return_document=ReturnDocument.AFTER)
        if not job:
            return False
        kind = self.kinds.get(job["kind"], {})
        url, body, headers = job["url"], job["body"], {"Idempotency-Key": job["key"]}
        if kind.get("build"):
            try:
                url, body, h2 = kind["build"](job)
                headers.update(h2 or {})
            except Exception as e:
                self._fail(job, f"build lỗi: {e}")
                return True
        status, resp, err = http_post(url, body, self.timeout_s, self.msglog, job.get("peer", ""), headers)
        res = (False, err) if err else kind.get("accept", _accept_2xx)(status, resp)
        ok, why, permanent = res[0], res[1], (len(res) > 2 and res[2])
        if permanent:
            # bên nhận từ chối hẳn (dữ liệu sai) - không gửi lại, báo cho nghiệp vụ xử lý
            self.coll.update_one({"_id": job["_id"]}, {"$set": {"status": DEAD, "last_error": str(why)[:500],
                                                               "resp": resp}})
            log.error("gửi %s bị từ chối hẳn: %s", job["key"], why)
            if kind.get("on_dead"):
                try:
                    kind["on_dead"](job, resp, why)
                except Exception:
                    log.exception("on_dead %s lỗi", job["key"])
            return True
        if ok:
            self.coll.update_one({"_id": job["_id"]}, {"$set": {"status": DONE, "done_at": now(),
                                                               "resp": resp, "last_error": None}})
            if kind.get("on_done"):
                try:
                    kind["on_done"](job, resp)
                except Exception:
                    log.exception("on_done %s lỗi", job["key"])
        else:
            self._fail(job, why or f"HTTP {status}")
        return True

    def _fail(self, job: dict, why: str):
        dead = self.max_tries and job["tries"] >= self.max_tries
        delay = min(self.retry_s * (2 ** min(job["tries"] - 1, 4)), 60)
        self.coll.update_one({"_id": job["_id"]}, {"$set": {
            "status": DEAD if dead else PENDING, "last_error": str(why)[:500],
            "next_at": now() + timedelta(seconds=delay)}})
        log.warning("gửi %s lỗi (lần %s): %s", job["key"], job["tries"], why)


def _accept_2xx(status: int, resp: Any) -> tuple[bool, str]:
    return (200 <= status < 300), f"HTTP {status}"


def accept_code(*codes) -> Callable[[int, Any], tuple[bool, str]]:
    """Thiết bị G8 (IDS/SCS/RCS): HTTP 200 và code thuộc `codes`."""
    def f(status: int, resp: Any):
        code = resp.get("code") if isinstance(resp, dict) else None
        desc = (resp.get("message") or resp.get("desc")) if isinstance(resp, dict) else resp
        if status == 200 and code in codes:
            return True, ""
        if status == 400 or (status == 200 and code == 1001 and desc not in (None, "", "fail")):
            # G8-ID trả 400/1001 khi dữ liệu lệnh sai, G8-Export trả 200/1001 kèm message: gửi lại cũng vô ích
            return False, f"HTTP {status} code={code} {desc}", True
        return False, f"HTTP {status} code={code} {desc}"
    return f
