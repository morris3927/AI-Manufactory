"""sentinel-core：FastAPI app；lifespan 啟動所有背景工作。

啟動：uvicorn sentinel.main:app --reload --port 8000（或 make dev）
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import routes_admin, routes_kb, routes_plant, routes_work, ws
from .runtime import Runtime
from .vision.ppe import YoloPpeDetector

log = logging.getLogger("sentinel")


def create_app(runtime_factory=None, *, start_kwargs: dict | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        rt: Runtime = runtime_factory() if runtime_factory else Runtime.from_config()
        app.state.rt = rt
        await rt.start(**(start_kwargs or {}))
        bot = None
        token = rt.settings.secret(rt.settings.secrets.discord_bot_token)
        if token:
            from .dispatch.discord_bot import run_bot

            bot = asyncio.create_task(run_bot(rt, token), name="discord.bot")
        try:
            yield
        finally:
            if bot:
                bot.cancel()
            await rt.stop()

    app = FastAPI(title="Sentinel AI — sentinel-core", version="0.1.0", lifespan=lifespan)
    # 本機任何埠＋區網 IP（Demo 用手機連筆電開 /m/{id}）；正式環境再收斂
    app.add_middleware(CORSMiddleware,
                       allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|10(\.\d+){3}|192\.168(\.\d+){2}|172\.(1[6-9]|2\d|3[01])(\.\d+){2})(:\d+)?",
                       allow_methods=["*"], allow_headers=["*"], allow_credentials=True)
    @app.middleware("http")
    async def judge_read_only(request: Request, call_next):
        """評審（X-Role: judge）唯讀：擋掉所有寫入操作（GV-6）。"""
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get("X-Role", "").lower() == "judge":
            return JSONResponse({"detail": "評審身分只能檢視，不能操作"}, status_code=403)
        return await call_next(request)

    for r in (routes_plant.router, routes_work.router, routes_admin.router, routes_kb.router):
        app.include_router(r, prefix="/api")
    app.include_router(ws.router)

    @app.get("/healthz")
    @app.get("/health")
    @app.get("/api/health")
    def health() -> dict:
        """系統狀態：偵測器實際模式（yolo／scripted）、embedding 模型、AI 開關，讓報告可以如實說明。"""
        rt: Runtime = app.state.rt
        s = rt.settings
        weights = s.base_dir / s.vision.yolo_weights
        det = rt.vision.ppe_detector
        mode = det.name if det is not None else ("yolo" if YoloPpeDetector.available(weights) else "scripted")
        store = rt.kb.store
        kb = rt.kb.health()
        return {
            "ok": True,
            "warnings": kb["warnings"],
            "detector_mode": mode,
            "yolo": {"weights": s.vision.yolo_weights, "weights_exist": weights.exists(),
                     "classes": getattr(det, "class_names", None) or None},
            "embedding_model": store.embedder.name if store else None,
            "kb": kb | {"allow_draft": s.kb.allow_draft},
            "jev": {"enabled": rt.jev.enabled, "model": s.jev.model},
            "llm": {"enabled": rt.llm.enabled, "model": s.llm.model},
            "notifier": rt.notifier.name,
            "workflow": {"require_permit": rt.workorders.require_permit},
            "scenario": rt.engine.scenario.id if rt.engine.scenario else None,
            # 每支攝影機實際播的是影片（公開照片合成的模擬監視畫面）還是程式合成畫面（找不到影片時的退路）
            "cameras": {cid: {"video": c.video, "source": "video" if c.synthetic is None else f"synthetic:{c.synthetic}",
                              "online": c.online} for cid, c in rt.cameras.items()},
        }

    return app


app = create_app()
