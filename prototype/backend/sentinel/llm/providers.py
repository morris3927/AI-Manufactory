"""LLM 介接層：Anthropic（Messages API）與 OpenAI（Chat Completions）轉接，對外介面跟 LlmClient（Ollama）一樣。

呼叫端（維修 Agent、診斷說明、案例整理）一律用 Ollama 格式：
- messages：{"role": "system"|"user"|"assistant"|"tool", "content": ...}；assistant 可帶 tool_calls，
  tool 訊息帶 tool_name（agent/tools.py 的 tool_result_message）
- tools：[{"type": "function", "function": {"name", "description", "parameters"}}]
- 回傳的 tool_calls：[{"id", "function": {"name", "arguments": dict}}]
- options：num_predict（最多輸出幾個 token）、temperature；think：True／False／None

切換方式見 config.yaml 的 llm.provider；金鑰從根目錄 .env 讀（ANTHROPIC_API_KEY／OPENAI_API_KEY）。
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from ..clock import Clock
from ..db.session import Database
from .client import LlmClient, LlmResult

DEFAULT_MAX_TOKENS = 2048          # Anthropic 一定要給 max_tokens；呼叫端沒給 num_predict 時用這個


def _args_dict(args: Any) -> dict:
    if isinstance(args, dict):
        return args
    try:
        v = json.loads(args or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return v if isinstance(v, dict) else {}


def _call_ids(tool_calls: list[dict], turn: int) -> list[str]:
    """Ollama 的 tool_calls 沒有 id；轉成 Claude／GPT 格式時要補上，後面的工具結果依序對回去。"""
    return [tc.get("id") or f"call_{turn}_{i}" for i, tc in enumerate(tool_calls)]


class AnthropicClient(LlmClient):
    """Claude Messages API（POST /v1/messages，串流為 SSE）。"""

    provider = "anthropic"
    API_VERSION = "2023-06-01"

    def _headers(self, api_key: str | None) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "anthropic-version": self.API_VERSION}
        if api_key:
            headers["x-api-key"] = api_key
        return headers

    @staticmethod
    def convert_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        """Ollama 格式 → (system, messages)。system 訊息併成一段；連續的工具結果併成同一則 user 訊息。"""
        system: list[str] = []
        out: list[dict[str, Any]] = []
        pending: list[str] = []
        for turn, m in enumerate(messages):
            role, content = m.get("role"), m.get("content") or ""
            if role == "system":
                system.append(content)
            elif role == "assistant" and m.get("tool_calls"):
                ids = _call_ids(m["tool_calls"], turn)
                blocks: list[dict[str, Any]] = [{"type": "text", "text": content}] if content else []
                for cid, tc in zip(ids, m["tool_calls"]):
                    fn = tc.get("function") or {}
                    blocks.append({"type": "tool_use", "id": cid, "name": fn.get("name", ""),
                                   "input": _args_dict(fn.get("arguments"))})
                out.append({"role": "assistant", "content": blocks})
                pending = list(ids)
            elif role == "tool":
                block = {"type": "tool_result", "tool_use_id": pending.pop(0) if pending else f"call_{turn}",
                         "content": content}
                prev = out[-1] if out else None
                if prev and prev["role"] == "user" and isinstance(prev["content"], list) and \
                        all(b.get("type") == "tool_result" for b in prev["content"]):
                    prev["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            else:
                out.append({"role": "assistant" if role == "assistant" else "user", "content": content})
        return "\n\n".join(system), out

    def _body(self, messages, tools, options, think, stream: bool) -> dict[str, Any]:
        system, msgs = self.convert_messages(messages)
        opts = options or {}
        body: dict[str, Any] = {"model": self.model, "max_tokens": int(opts.get("num_predict") or DEFAULT_MAX_TOKENS),
                                "messages": msgs, "stream": stream}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                              "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}}
                             for t in tools]
        # temperature 不轉送：Sonnet 5 等新模型不接受取樣參數（會回 400）
        if think is False:
            body["thinking"] = {"type": "disabled"}          # 維修 Agent 要首字快：關掉思考
        elif think is True:
            body["thinking"] = {"type": "adaptive"}
        return body

    def _parse(self, d: dict[str, Any]) -> LlmResult:
        text, calls = [], []
        for b in d.get("content") or []:
            if b.get("type") == "text":
                text.append(b.get("text") or "")
            elif b.get("type") == "tool_use":
                calls.append({"id": b.get("id"), "function": {"name": b.get("name"), "arguments": b.get("input") or {}}})
        u = d.get("usage") or {}
        tokens = (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
        return LlmResult(content="".join(text), tool_calls=calls, model=d.get("model", self.model), tokens=tokens or None)

    def _parse_line(self, line: str, st: dict[str, Any]) -> str | None:
        if not line.startswith("data:"):
            return None                                    # event: 行、註解行
        d = json.loads(line[5:].strip())
        kind = d.get("type")
        blocks: dict[int, dict] = st.setdefault("blocks", {})
        if kind == "error":
            raise RuntimeError(str((d.get("error") or {}).get("message") or d.get("error")))
        if kind == "message_start":
            msg = d.get("message") or {}
            st["model"] = msg.get("model", self.model)
            st["in_tokens"] = (msg.get("usage") or {}).get("input_tokens") or 0
        elif kind == "content_block_start":
            b = d.get("content_block") or {}
            if b.get("type") == "tool_use":
                blocks[d.get("index", 0)] = {"id": b.get("id"), "name": b.get("name"), "json": ""}
        elif kind == "content_block_delta":
            delta = d.get("delta") or {}
            if delta.get("type") == "text_delta":
                return delta.get("text") or None
            if delta.get("type") == "input_json_delta" and d.get("index", 0) in blocks:
                blocks[d.get("index", 0)]["json"] += delta.get("partial_json") or ""
        elif kind == "content_block_stop":
            b = blocks.pop(d.get("index", 0), None)
            if b is not None:
                st["tool_calls"].append({"id": b["id"], "function": {"name": b["name"],
                                                                     "arguments": json.loads(b["json"] or "{}")}})
        elif kind == "message_delta":
            out = (d.get("usage") or {}).get("output_tokens") or 0
            st["tokens"] = (st.get("in_tokens") or 0) + out or None
        return None


class OpenAIClient(LlmClient):
    """OpenAI Chat Completions（POST /v1/chat/completions，串流為 SSE，最後一行 data: [DONE]）。"""

    provider = "openai"

    @staticmethod
    def convert_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        pending: list[str] = []
        for turn, m in enumerate(messages):
            role, content = m.get("role"), m.get("content") or ""
            if role == "assistant" and m.get("tool_calls"):
                ids = _call_ids(m["tool_calls"], turn)
                out.append({"role": "assistant", "content": content or None, "tool_calls": [
                    {"id": cid, "type": "function",
                     "function": {"name": (tc.get("function") or {}).get("name", ""),
                                  "arguments": json.dumps(_args_dict((tc.get("function") or {}).get("arguments")),
                                                          ensure_ascii=False)}}
                    for cid, tc in zip(ids, m["tool_calls"])]})
                pending = list(ids)
            elif role == "tool":
                out.append({"role": "tool", "tool_call_id": pending.pop(0) if pending else f"call_{turn}",
                            "content": content})
            else:
                out.append({"role": role, "content": content})
        return out

    def _body(self, messages, tools, options, think, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "messages": self.convert_messages(messages), "stream": stream}
        if stream:
            body["stream_options"] = {"include_usage": True}      # 最後一段帶 token 用量
        if tools:
            body["tools"] = tools                                  # 工具定義本來就是 OpenAI 格式
        opts = options or {}
        if opts.get("num_predict"):
            body["max_completion_tokens"] = int(opts["num_predict"])
        if opts.get("temperature") is not None:
            body["temperature"] = opts["temperature"]
        return body                                                # think 沒有對應參數，略過

    def _parse(self, d: dict[str, Any]) -> LlmResult:
        msg = ((d.get("choices") or [{}])[0]).get("message") or {}
        calls = [{"id": tc.get("id"), "function": {"name": (tc.get("function") or {}).get("name"),
                                                   "arguments": _args_dict((tc.get("function") or {}).get("arguments"))}}
                 for tc in msg.get("tool_calls") or []]
        tokens = (d.get("usage") or {}).get("total_tokens")
        return LlmResult(content=msg.get("content") or "", tool_calls=calls, model=d.get("model", self.model),
                         tokens=tokens or None)

    def _parse_line(self, line: str, st: dict[str, Any]) -> str | None:
        if not line.startswith("data:"):
            return None
        data = line[5:].strip()
        partial: dict[int, dict] = st.setdefault("partial", {})
        if data == "[DONE]":
            for i in sorted(partial):
                p = partial[i]
                st["tool_calls"].append({"id": p["id"], "function": {"name": p["name"],
                                                                     "arguments": json.loads(p["args"] or "{}")}})
            partial.clear()
            return None
        d = json.loads(data)
        if d.get("error"):
            raise RuntimeError(str((d["error"] or {}).get("message") if isinstance(d["error"], dict) else d["error"]))
        st["model"] = d.get("model") or st.get("model")
        if u := d.get("usage"):
            st["tokens"] = u.get("total_tokens") or None
        text = None
        for ch in d.get("choices") or []:
            delta = ch.get("delta") or {}
            for tc in delta.get("tool_calls") or []:
                p = partial.setdefault(tc.get("index", 0), {"id": None, "name": "", "args": ""})
                p["id"] = tc.get("id") or p["id"]
                fn = tc.get("function") or {}
                p["name"] += fn.get("name") or ""
                p["args"] += fn.get("arguments") or ""
            if delta.get("content"):
                text = (text or "") + delta["content"]
        return text


PROVIDERS: dict[str, type[LlmClient]] = {"ollama": LlmClient, "anthropic": AnthropicClient, "openai": OpenAIClient}


def make_llm_client(provider: str, *, api_key: str | None, url: str, model: str, timeout_s: float = 30.0,
                    db: Database | None = None, clock: Clock | None = None, enabled: bool = True,
                    transport: httpx.AsyncBaseTransport | None = None) -> LlmClient:
    try:
        cls = PROVIDERS[provider]
    except KeyError:
        raise ValueError(f"不支援的 llm.provider：{provider}（可用：{'、'.join(PROVIDERS)}）") from None
    return cls(api_key=api_key, url=url, model=model, timeout_s=timeout_s, db=db, clock=clock, enabled=enabled,
               transport=transport)
