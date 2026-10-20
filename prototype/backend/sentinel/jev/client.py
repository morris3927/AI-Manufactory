"""Jev client：OpenRouter Decisions API（不是 chat completions）。

- httpx.AsyncClient 連線保持不斷；啟動時先送一次暖機請求。
- 逾時 3 秒或任何錯誤 → 拋出 JevUnavailable，呼叫端要有退路（規則照常派工）。
- 每次呼叫寫入 jev_call 表：purpose、題目版本、延遲、cost、model、輸入摘要、答案。
"""
from __future__ import annotations

import logging
import time
from typing import Annotated, Any, Literal

import httpx
from pydantic import BaseModel, Field, TypeAdapter

from ..clock import Clock
from ..db.models import JevCall
from ..db.session import Database
from ..ids import jsonable

log = logging.getLogger(__name__)


class JevUnavailable(Exception):
    """Jev 逾時、錯誤或停用。"""


# ── 回傳解析（Jev整合規劃.md 3.1）──────────────────────────────
class NoulAnswer(BaseModel):
    type: Literal["noul"]
    noul: float

    @property
    def p(self) -> float:
        return self.noul

    @property
    def confidence(self) -> float:
        return abs(2 * self.noul - 1)


class ChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str
    confidence: float = 0.0
    probabilities: dict[str, float] = Field(default_factory=dict)

    def top(self, k: int = 3) -> list[tuple[str, float]]:
        return sorted(self.probabilities.items(), key=lambda kv: -kv[1])[:k]


class ScoreAnswer(BaseModel):
    type: Literal["score"]
    score: float                          # 機率加權的級數，從 0 起算
    confidence: float = 0.0
    probabilities: dict[str, float] = Field(default_factory=dict)
    legend: dict[str, str] = Field(default_factory=dict)


class BoxValue(BaseModel):
    top: float
    bottom: float
    left: float
    right: float


class BoxAnswer(BaseModel):
    type: Literal["box"]
    box: BoxValue
    confidence: float = 0.0


Answer = Annotated[NoulAnswer | ChoiceAnswer | ScoreAnswer | BoxAnswer, Field(discriminator="type")]
_answers = TypeAdapter(dict[str, Answer])


class JevResult(BaseModel):
    id: str | None = None
    model: str | None = None
    provider: str | None = None
    answers: dict[str, Answer]
    input_tokens: int | None = None
    cost: float | None = None
    latency_ms: float | None = None
    questions_ver: str | None = None

    def noul(self, key: str) -> float:
        a = self.answers[key]
        assert isinstance(a, NoulAnswer)
        return a.noul

    def choice(self, key: str) -> ChoiceAnswer:
        a = self.answers[key]
        assert isinstance(a, ChoiceAnswer)
        return a

    def score(self, key: str) -> ScoreAnswer:
        a = self.answers[key]
        assert isinstance(a, ScoreAnswer)
        return a

    def box(self, key: str) -> BoxAnswer | None:
        a = self.answers.get(key)
        return a if isinstance(a, BoxAnswer) else None


def parse_response(data: dict[str, Any]) -> JevResult:
    usage = data.get("usage") or {}
    return JevResult(id=data.get("id"), model=data.get("model"), provider=data.get("provider"),
                     answers=_answers.validate_python(data.get("answers") or {}),
                     input_tokens=usage.get("input_tokens"), cost=usage.get("cost"))


def summarize_state(state: Any) -> Any:
    """輸入摘要：圖片換成占位字串，不寫入 base64。"""
    if isinstance(state, dict):
        if state.get("type") == "image_url":
            url = (state.get("image_url") or {}).get("url", "")
            return f"<image {len(url) * 3 // 4 // 1024} KB>"
        return {k: summarize_state(v) for k, v in state.items()}
    if isinstance(state, list):
        return [summarize_state(v) for v in state]
    return state


class JevClient:
    def __init__(self, *, api_key: str | None, url: str, model: str, timeout_s: float = 3.0,
                 db: Database | None = None, clock: Clock | None = None, enabled: bool = True,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.url, self.model, self.timeout_s = url, model, timeout_s
        self.db, self.clock = db, clock or Clock()
        self.enabled = enabled and bool(api_key)
        self.has_key = bool(api_key)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self.http = httpx.AsyncClient(timeout=timeout_s, headers=headers, transport=transport,
                                      limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=300))

    async def warmup(self) -> bool:
        try:
            await self.ask({"ping": "warmup"}, {"ok": {"type": "noul", "instructions": "這是暖機請求嗎？"}},
                           purpose="warmup", questions_ver="warmup")
            return True
        except JevUnavailable as e:
            log.warning("Jev 暖機失敗：%s", e)
            return False

    async def ask(self, state: dict, questions: dict, *, purpose: str, trace_id: str | None = None,
                  questions_ver: str = "") -> JevResult:
        if not self.enabled:
            raise JevUnavailable("Jev 已停用或沒有金鑰")
        t0 = time.perf_counter()
        err: str | None = None
        result: JevResult | None = None
        try:
            r = await self.http.post(self.url, json={"model": self.model, "state": state, "questions": questions})
            r.raise_for_status()
            result = parse_response(r.json())
            missing = set(questions) - set(result.answers)
            if missing:
                raise ValueError(f"缺少答案：{sorted(missing)}")
        except httpx.TimeoutException:
            err = f"timeout>{self.timeout_s}s"
        except httpx.HTTPStatusError as e:
            err = f"http {e.response.status_code}"
        except Exception as e:  # 連線錯誤、格式錯誤
            err = f"{type(e).__name__}: {e}"
        latency = (time.perf_counter() - t0) * 1000
        if result is not None and err is None:
            result.latency_ms, result.questions_ver = latency, questions_ver
        self._log(purpose, trace_id, questions_ver, state, result if err is None else None, latency, err)
        if err is not None:
            raise JevUnavailable(err)
        assert result is not None
        return result

    def _log(self, purpose, trace_id, ver, state, result, latency, err) -> None:
        if self.db is None or purpose == "warmup":
            return
        self.db.add(JevCall(
            trace_id=trace_id, purpose=purpose, questions_ver=ver, model=result.model if result else self.model,
            latency_ms=round(latency, 1), cost=result.cost if result else None,
            input_summary=jsonable(summarize_state(state)),
            answers=jsonable({k: v.model_dump() for k, v in result.answers.items()}) if result else {},
            error=err, ts=self.clock.now(),
        ))

    async def aclose(self) -> None:
        await self.http.aclose()
