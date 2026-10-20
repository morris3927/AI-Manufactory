"""DeepSeek（Ollama Cloud /api/chat）。每次呼叫寫入 llm_call 表。"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

from ..clock import Clock
from ..db.models import LlmCall
from ..db.session import Database

log = logging.getLogger(__name__)


class LlmUnavailable(Exception):
    pass


class LlmResult(BaseModel):
    content: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    model: str | None = None
    latency_ms: float | None = None
    tokens: int | None = None


class LlmClient:
    def __init__(self, *, api_key: str | None, url: str, model: str, timeout_s: float = 30.0,
                 db: Database | None = None, clock: Clock | None = None, enabled: bool = True,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.url, self.model = url, model
        self.db, self.clock = db, clock or Clock()
        self.enabled = enabled and bool(api_key)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self.http = httpx.AsyncClient(timeout=timeout_s, headers=headers, transport=transport)
        self.calls = 0

    async def chat(self, messages: list[dict[str, Any]], *, tools: list[dict] | None = None, purpose: str,
                   trace_id: str | None = None, options: dict | None = None) -> LlmResult:
        if not self.enabled:
            raise LlmUnavailable("LLM 已停用或沒有金鑰")
        self.calls += 1
        body: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False}
        if tools:
            body["tools"] = tools
        if options:
            body["options"] = options
        t0 = time.perf_counter()
        err, result = None, None
        try:
            r = await self.http.post(self.url, json=body)
            r.raise_for_status()
            d = r.json()
            msg = d.get("message") or {}
            tokens = (d.get("prompt_eval_count") or 0) + (d.get("eval_count") or 0)
            result = LlmResult(content=msg.get("content") or "", tool_calls=msg.get("tool_calls") or [],
                               model=d.get("model", self.model), tokens=tokens or None)
        except httpx.TimeoutException:
            err = "timeout"
        except httpx.HTTPStatusError as e:
            err = f"http {e.response.status_code}"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        latency = (time.perf_counter() - t0) * 1000
        if self.db is not None:
            self.db.add(LlmCall(trace_id=trace_id, purpose=purpose, model=result.model if result else self.model,
                                latency_ms=round(latency, 1), tokens=result.tokens if result else None, error=err,
                                ts=self.clock.now()))
        if err or result is None:
            raise LlmUnavailable(err or "unknown")
        result.latency_ms = latency
        return result

    async def aclose(self) -> None:
        await self.http.aclose()
