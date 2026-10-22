"""DeepSeek（Ollama Cloud /api/chat）。每次呼叫寫入 llm_call 表。

- chat()：一次拿完整回覆；stream_chat()：stream=true，逐段回傳文字（NDJSON），最後回傳 LlmResult。
- think：DeepSeek V4.1 Flash 預設會先「思考」數百個 token（實測首字 3 s 以上）；維修 Agent 傳 think=False。
- 連線保持（keepalive 300 s）；啟動時 warmup() 先建好 TLS 連線。
"""
from __future__ import annotations

import logging
import json
import time
from collections.abc import AsyncIterator
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
    ttft_ms: float | None = None          # 串流：第一個文字 token 的時間


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
        self.http = httpx.AsyncClient(timeout=timeout_s, headers=headers, transport=transport,
                                      limits=httpx.Limits(max_keepalive_connections=8, keepalive_expiry=300))
        self.calls = 0

    def _body(self, messages, tools, options, think, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "stream": stream}
        if tools:
            body["tools"] = tools
        if options:
            body["options"] = options
        if think is not None:
            body["think"] = think
        return body

    async def warmup(self) -> bool:
        """建立連線（TLS）並讓雲端載入模型；失敗只記錄。"""
        if not self.enabled:
            return False
        try:
            r = await self.http.post(self.url, json=self._body([{"role": "user", "content": "ping"}], None,
                                                               {"num_predict": 1}, False, False))
            r.raise_for_status()
            return True
        except Exception as e:
            log.warning("LLM 暖機失敗：%s", e)
            return False

    def _log(self, purpose, trace_id, result: LlmResult | None, latency: float, err: str | None) -> None:
        if self.db is not None:
            self.db.add(LlmCall(trace_id=trace_id, purpose=purpose, model=result.model if result else self.model,
                                latency_ms=round(latency, 1), tokens=result.tokens if result else None, error=err,
                                ts=self.clock.now()))

    async def stream_chat(self, messages: list[dict[str, Any]], *, tools: list[dict] | None = None, purpose: str,
                          trace_id: str | None = None, options: dict | None = None,
                          think: bool | None = None) -> AsyncIterator[str | LlmResult]:
        """逐段 yield 文字（str）；最後 yield 一個 LlmResult（完整內容、tool_calls、延遲）。錯誤 → LlmUnavailable。"""
        if not self.enabled:
            raise LlmUnavailable("LLM 已停用或沒有金鑰")
        self.calls += 1
        t0 = time.perf_counter()
        parts: list[str] = []
        tool_calls: list[dict] = []
        first: float | None = None
        last: dict[str, Any] = {}
        err: str | None = None
        try:
            async with self.http.stream("POST", self.url,
                                        json=self._body(messages, tools, options, think, True)) as r:
                if r.status_code >= 400:
                    await r.aread()
                    r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    if d.get("error"):
                        raise RuntimeError(str(d["error"]))
                    msg = d.get("message") or {}
                    tool_calls += msg.get("tool_calls") or []
                    if text := msg.get("content"):
                        if first is None:
                            first = time.perf_counter()
                        parts.append(text)
                        yield text
                    if d.get("done"):
                        last = d
        except httpx.TimeoutException:
            err = "timeout"
        except httpx.HTTPStatusError as e:
            err = f"http {e.response.status_code}"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        latency = (time.perf_counter() - t0) * 1000
        result = None if err else LlmResult(
            content="".join(parts), tool_calls=tool_calls, model=last.get("model", self.model),
            tokens=((last.get("prompt_eval_count") or 0) + (last.get("eval_count") or 0)) or None,
            latency_ms=latency, ttft_ms=round((first - t0) * 1000, 1) if first else None)
        self._log(purpose, trace_id, result, latency, err)
        if result is None:
            raise LlmUnavailable(err or "unknown")
        yield result

    async def chat(self, messages: list[dict[str, Any]], *, tools: list[dict] | None = None, purpose: str,
                   trace_id: str | None = None, options: dict | None = None, think: bool | None = None) -> LlmResult:
        if not self.enabled:
            raise LlmUnavailable("LLM 已停用或沒有金鑰")
        self.calls += 1
        body = self._body(messages, tools, options, think, False)
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
        self._log(purpose, trace_id, result, latency, err)
        if err or result is None:
            raise LlmUnavailable(err or "unknown")
        result.latency_ms = latency
        return result

    async def aclose(self) -> None:
        await self.http.aclose()
