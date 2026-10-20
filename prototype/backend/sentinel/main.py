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

from .api import routes_admin, routes_plant, routes_work, ws
from .runtime import Runtime

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
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
                       allow_methods=["*"], allow_headers=["*"], allow_credentials=True)
    @app.middleware("http")
    async def judge_read_only(request: Request, call_next):
        """評審（X-Role: judge）唯讀：擋掉所有寫入操作（GV-6）。"""
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get("X-Role", "").lower() == "judge":
            return JSONResponse({"detail": "評審身分只能檢視，不能操作"}, status_code=403)
        return await call_next(request)

    for r in (routes_plant.router, routes_work.router, routes_admin.router):
        app.include_router(r, prefix="/api")
    app.include_router(ws.router)

    @app.get("/healthz")
    def healthz() -> dict:
        rt: Runtime = app.state.rt
        return {"ok": True, "jev": rt.jev.enabled, "llm": rt.llm.enabled, "notifier": rt.notifier.name,
                "scenario": rt.engine.scenario.id if rt.engine.scenario else None}

    return app


app = create_app()
