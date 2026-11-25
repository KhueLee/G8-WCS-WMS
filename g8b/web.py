"""Site giao diện chung của luồng B: một trang cho cả WMS ảo và WCS B.

Phục vụ giao diện (g8b/static) và chuyển tiếp API:
  /wms/...  -> WMS ảo (mặc định http://127.0.0.1:9100)
  /wcs/...  -> WCS B  (mặc định http://127.0.0.1:9000)
Hai app vẫn chạy độc lập (có thể ở 2 máy khác nhau); site chỉ gom giao diện.
Chạy: python -m g8b.web
"""
from __future__ import annotations

import logging
import os

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .common import ROOT, load_config, setup_logging

VERSION = "0.6.0"
DEFAULTS: dict = {
    "server": {"host": "0.0.0.0", "port": 5510},
    "wms_url": "http://127.0.0.1:9100",
    "wcs_url": "http://127.0.0.1:9000",
    "timeout_s": 30,
}
GUIDE = os.path.join(ROOT, "docs", "HUONG_DAN_SU_DUNG.pdf")
HOP = {"connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length", "host"}
log = logging.getLogger("web")


def build_app(cfg: dict) -> FastAPI:
    app = FastAPI(title="G8 luồng B", version=VERSION)
    static = os.path.join(ROOT, "g8b", "static")
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.middleware("http")
    async def no_cache_static(req: Request, call_next):
        # trình duyệt luôn hỏi lại file giao diện, sửa giao diện xong chỉ cần F5
        resp = await call_next(req)
        if req.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp
    client = httpx.AsyncClient(timeout=cfg["timeout_s"], trust_env=False)
    targets = {"wms": cfg["wms_url"].rstrip("/"), "wcs": cfg["wcs_url"].rstrip("/")}

    @app.get("/")
    def index():
        return FileResponse(os.path.join(static, "index.html"), headers={"Cache-Control": "no-store"})

    @app.get("/huong-dan")
    def guide():
        if not os.path.exists(GUIDE):
            return JSONResponse({"ok": False, "msg": "Chưa có file docs/HUONG_DAN_SU_DUNG.pdf"}, status_code=404)
        return FileResponse(GUIDE, media_type="application/pdf",
                            headers={"Content-Disposition": 'inline; filename="HUONG_DAN_SU_DUNG.pdf"'})

    @app.get("/api/web")
    def info():
        return {"version": VERSION, "wms_url": targets["wms"], "wcs_url": targets["wcs"]}

    @app.api_route("/{app_name}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def proxy(app_name: str, path: str, request: Request):
        base = targets.get(app_name)
        if not base:
            return JSONResponse({"ok": False, "msg": "không có trang này"}, status_code=404)
        url = f"{base}/{path}"
        if request.url.query:
            url += "?" + request.url.query
        try:
            r = await client.request(request.method, url, content=await request.body(),
                                     headers={k: v for k, v in request.headers.items() if k.lower() not in HOP})
        except httpx.HTTPError as e:
            log.warning("Không kết nối được %s: %s", base, e)
            return JSONResponse({"ok": False, "msg": f"Không kết nối được {app_name.upper()} ({base}): {e}"}, status_code=502)
        return Response(r.content, status_code=r.status_code,
                        headers={k: v for k, v in r.headers.items() if k.lower() not in HOP})

    return app


def main():
    cfg = load_config("web", DEFAULTS)
    setup_logging("web", os.environ.get("G8B_LOG_DIR") and os.path.join(os.environ["G8B_LOG_DIR"], "web"))
    log.info("Site G8 luồng B v%s: WMS %s, WCS %s", VERSION, cfg["wms_url"], cfg["wcs_url"])
    uvicorn.run(build_app(cfg), host=cfg["server"]["host"], port=cfg["server"]["port"], log_level="warning")


if __name__ == "__main__":
    main()
