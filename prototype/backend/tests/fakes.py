"""測試用的假 Jev／LLM（固定值，結果可重現）。走真的 JevClient／LlmClient，只換掉 HTTP 傳輸層。"""
from __future__ import annotations

import json
from typing import Callable

import httpx

JEV_SAMPLE = {   # Jev整合規劃.md 3.1 的實際回應
    "id": "gen-dec-...",
    "model": "typesafe/jev-1.14-20261014",
    "provider": "TypeSafe",
    "answers": {
        "is_bug": {"type": "noul", "noul": 0.96},
        "team": {"type": "choice", "choice": "payments", "confidence": 0.67,
                 "probabilities": {"payments": 0.78, "frontend": 0.22, "account": 0}},
        "urgency": {"type": "score", "score": 1.99, "confidence": 0.99,
                    "probabilities": {"0": 0, "1": 0, "2": 1},
                    "legend": {"0": "...", "1": "...", "2": "..."}},
        "where": {"type": "box", "box": {"top": 0.49, "bottom": 0.89, "left": 0.58, "right": 0.96},
                  "confidence": 0.93},
    },
    "usage": {"input_tokens": 476, "output_tokens": 70, "cost": 0.000019992},
}

RC_PROBS = [0.82, 0.09, 0.05, 0.03, 0.01]


class FakeJev:
    """依題目 key 回答；看圖題依模擬時間決定（t ≥ jam_from_s 才判定卡料）。"""

    def __init__(self, t_fn: Callable[[], float] = lambda: 0.0, *, jam_from_s: float = 10.0,
                 p_false_alarm: float = 0.04, severity_score: float = 1.0, fail: bool = False,
                 smoke_from_s: float = 8.0):
        self.t_fn = t_fn
        self.jam_from_s = jam_from_s
        self.p_false_alarm = p_false_alarm
        self.severity_score = severity_score
        self.fail = fail
        self.smoke_from_s = smoke_from_s
        self.calls: list[dict] = []

    def purposes(self) -> list[str]:
        return [next(iter(c["questions"])) for c in self.calls]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            raise httpx.ReadTimeout("fake timeout", request=request)
        body = json.loads(request.content)
        self.calls.append(body)
        answers = {k: self.answer(k, spec, body["state"]) for k, spec in body["questions"].items()}
        return httpx.Response(200, json={"id": "gen-dec-fake", "model": "typesafe/jev-1.14-20261014",
                                         "provider": "TypeSafe", "answers": answers,
                                         "usage": {"input_tokens": 120, "output_tokens": 0, "cost": 0.00002}})

    def answer(self, key: str, spec: dict, state: dict) -> dict:
        t = self.t_fn()
        if key == "jammed":
            return {"type": "noul", "noul": 0.85 if t >= self.jam_from_s else 0.08}
        if key == "smoke_outside_cell":
            return {"type": "noul", "noul": 0.9 if t >= self.smoke_from_s else 0.05}
        if key == "flame_outside_cell":
            return {"type": "noul", "noul": 0.04}
        if key == "smoke_box":
            return {"type": "box", "box": {"top": 0.05, "bottom": 0.45, "left": 0.7, "right": 0.95}, "confidence": 0.7}
        if key == "jam_box":
            return {"type": "box", "box": {"top": 0.3, "bottom": 0.7, "left": 0.2, "right": 0.8}, "confidence": 0.8}
        if key == "is_false_alarm":
            return {"type": "noul", "noul": self.p_false_alarm}
        if key == "category":
            return {"type": "choice", "choice": "equipment", "confidence": 0.9,
                    "probabilities": {"safety": 0.02, "equipment": 0.95, "quality": 0.02, "false_alarm": 0.01}}
        if key == "severity":
            return {"type": "score", "score": self.severity_score, "confidence": 0.9,
                    "probabilities": {"0": 0.05, "1": 0.9, "2": 0.05}}
        if spec["type"] == "choice":
            keys = list(spec["criteria"])
            probs = {k: (RC_PROBS[i] if i < len(RC_PROBS) else 0.0) for i, k in enumerate(keys)}
            top = max(probs, key=probs.get)
            return {"type": "choice", "choice": top, "confidence": 0.7, "probabilities": probs}
        if key == "enters_danger_zone":
            return {"type": "noul", "noul": 0.9 if "伸手" in str(state.get("step")) else 0.05}
        if key == "needs_loto":
            return {"type": "noul", "noul": 0.8}          # live 觀察：LOTO 後 needs_loto 仍常 0.4–0.86
        if key == "high_voltage":
            return {"type": "noul", "noul": 0.1}
        if key in ("has_cause", "has_action", "has_result"):
            return {"type": "noul", "noul": 0.9 if len(str(state.get("report", ""))) >= 20 else 0.1}
        if key == "complete":
            return {"type": "noul", "noul": 0.92 if len(str(state.get("report", ""))) >= 20 else 0.2}
        if spec["type"] == "box":
            return {"type": "box", "box": {"top": 0, "bottom": 1, "left": 0, "right": 1}, "confidence": 0.5}
        return {"type": "noul", "noul": 0.02}


AGENT_TEXT = ("目前先驗證送料滾輪磨損（機率 82%）。\n"
              "1. 目視上下滾輪表面是否有亮帶或油污\n"
              "2. 伸手取出卡在送料口的板材\n"
              "3. 需要通電量測送料長度時，依測試／定位程序由原上鎖人取下自己的鎖、現場主管在場\n"
              "4. 處理完成後即可復電試運轉。")


class FakeLlm:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            raise httpx.ConnectError("fake down", request=request)
        body = json.loads(request.content)
        self.calls.append(body)
        is_agent = body["messages"][0]["content"].startswith("你是 Sentinel AI 的維修助理")
        content = AGENT_TEXT if is_agent else "送料機 FD02 卡料，最可能是送料滾輪磨損（82%）。請先依 LOTO 上鎖後檢查滾輪。"
        return httpx.Response(200, json={"model": "deepseek-v4.1-flash", "message": {"role": "assistant", "content": content},
                                         "prompt_eval_count": 50, "eval_count": 40, "done": True})
