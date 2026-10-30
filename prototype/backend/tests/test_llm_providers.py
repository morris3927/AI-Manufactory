"""LLM 介接層：Anthropic（Messages API）、OpenAI（Chat Completions）轉接。不打真的 API，用 httpx.MockTransport
驗證送出的 request 格式與回應解析（含串流、tool calling），對外介面要跟 Ollama 版 LlmClient 一樣。"""
from __future__ import annotations

import json

import httpx
import pytest

from sentinel.config import LlmCfg, Secrets, Settings
from sentinel.llm.client import LlmClient, LlmResult, LlmUnavailable
from sentinel.llm.providers import AnthropicClient, OpenAIClient, make_llm_client

TOOLS = [{"type": "function", "function": {"name": "get_sop", "description": "查 SOP",
                                           "parameters": {"type": "object", "properties": {"phase": {"type": "string"}}}}}]
# 維修 Agent 工具輪的訊息（Ollama 格式：tool_calls 沒有 id、工具結果用 tool_name）
MESSAGES = [
    {"role": "system", "content": "你是 Sentinel AI 的維修助理"},
    {"role": "user", "content": "下一步？"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "get_sop", "arguments": {"phase": "verify"}}},
        {"function": {"name": "get_sop", "arguments": {"phase": "repair"}}}]},
    {"role": "tool", "tool_name": "get_sop", "content": "{\"text\": \"驗證\"}"},
    {"role": "tool", "tool_name": "get_sop", "content": "{\"text\": \"修理\"}"},
]


class Capture:
    def __init__(self, respond):
        self.respond, self.requests = respond, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(json.loads(request.content))

    @property
    def body(self) -> dict:
        return json.loads(self.requests[-1].content)


def sse(events: list[dict], *, done: bool = False) -> bytes:
    out = "".join(f"event: {e.get('type', 'message')}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events)
    return (out + ("data: [DONE]\n\n" if done else "")).encode()


async def collect(client: LlmClient, **kw) -> tuple[list[str], LlmResult]:
    parts, res = [], None
    async for item in client.stream_chat(MESSAGES, purpose="test", **kw):
        if isinstance(item, LlmResult):
            res = item
        else:
            parts.append(item)
    assert res is not None
    return parts, res


# ── Anthropic ───────────────────────────────────────────────────
def anthropic(handler) -> AnthropicClient:
    return AnthropicClient(api_key="sk-ant-test", url="https://api.anthropic.com/v1/messages", model="claude-sonnet-5",
                           transport=httpx.MockTransport(handler))


async def test_anthropic_request_format_and_parse():
    cap = Capture(lambda b: httpx.Response(200, json={
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5",
        "content": [{"type": "text", "text": "先查 SOP。"},
                    {"type": "tool_use", "id": "toolu_9", "name": "get_sop", "input": {"phase": "inspect"}}],
        "stop_reason": "tool_use", "usage": {"input_tokens": 120, "output_tokens": 30}}))
    c = anthropic(cap)
    res = await c.chat(MESSAGES, tools=TOOLS, purpose="test", options={"num_predict": 700, "temperature": 0.2},
                       think=False)
    req = cap.requests[-1]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "sk-ant-test" and req.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in req.headers
    b = cap.body
    assert b["model"] == "claude-sonnet-5" and b["max_tokens"] == 700 and b["stream"] is False
    assert b["system"] == "你是 Sentinel AI 的維修助理" and b["thinking"] == {"type": "disabled"}
    assert "temperature" not in b                                 # 新模型不接受取樣參數
    assert b["tools"] == [{"name": "get_sop", "description": "查 SOP", "input_schema": TOOLS[0]["function"]["parameters"]}]
    assert b["messages"][0] == {"role": "user", "content": "下一步？"}
    use = b["messages"][1]
    assert use["role"] == "assistant" and [x["type"] for x in use["content"]] == ["tool_use", "tool_use"]
    assert use["content"][0]["input"] == {"phase": "verify"}
    ids = [x["id"] for x in use["content"]]
    results = b["messages"][2]                                     # 兩個工具結果併成同一則 user 訊息，id 依序對上
    assert results["role"] == "user" and [x["tool_use_id"] for x in results["content"]] == ids
    assert results["content"][1]["content"] == "{\"text\": \"修理\"}"
    assert len(b["messages"]) == 3

    assert res.content == "先查 SOP。" and res.model == "claude-sonnet-5" and res.tokens == 150
    assert res.tool_calls == [{"id": "toolu_9", "function": {"name": "get_sop", "arguments": {"phase": "inspect"}}}]
    assert res.latency_ms is not None


async def test_anthropic_stream_text_and_tool_use():
    events = [
        {"type": "message_start", "message": {"model": "claude-sonnet-5", "usage": {"input_tokens": 80, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "ping"},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "目前先驗證"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "送料滾輪磨損。"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "toolu_1", "name": "get_sop", "input": {}}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"phase\": "}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "\"verify\"}"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 25}},
        {"type": "message_stop"},
    ]
    cap = Capture(lambda b: httpx.Response(200, content=sse(events), headers={"content-type": "text/event-stream"}))
    parts, res = await collect(anthropic(cap), tools=TOOLS, options={"num_predict": 700}, think=False)
    assert cap.body["stream"] is True
    assert parts == ["目前先驗證", "送料滾輪磨損。"]
    assert res.content == "目前先驗證送料滾輪磨損。" and res.model == "claude-sonnet-5" and res.tokens == 105
    assert res.tool_calls == [{"id": "toolu_1", "function": {"name": "get_sop", "arguments": {"phase": "verify"}}}]
    assert res.ttft_ms is not None and res.latency_ms is not None


async def test_anthropic_errors_raise_unavailable():
    err = [{"type": "message_start", "message": {"model": "claude-sonnet-5", "usage": {"input_tokens": 1}}},
           {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}]
    c = anthropic(lambda r: httpx.Response(200, content=sse(err), headers={"content-type": "text/event-stream"}))
    with pytest.raises(LlmUnavailable, match="Overloaded"):
        await collect(c)
    c = anthropic(lambda r: httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error"}}))
    with pytest.raises(LlmUnavailable, match="http 401"):
        await c.chat(MESSAGES, purpose="test")
    no_key = AnthropicClient(api_key=None, url="https://api.anthropic.com/v1/messages", model="claude-sonnet-5")
    assert no_key.enabled is False


# ── OpenAI ──────────────────────────────────────────────────────
def openai(handler) -> OpenAIClient:
    return OpenAIClient(api_key="sk-test", url="https://api.openai.com/v1/chat/completions", model="gpt-test",
                        transport=httpx.MockTransport(handler))


async def test_openai_request_format_and_parse():
    cap = Capture(lambda b: httpx.Response(200, json={
        "id": "chatcmpl-1", "model": "gpt-test",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_a", "type": "function",
                            "function": {"name": "get_sop", "arguments": "{\"phase\":\"inspect\"}"}}]}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}))
    res = await openai(cap).chat(MESSAGES, tools=TOOLS, purpose="test", options={"num_predict": 700}, think=False)
    req = cap.requests[-1]
    assert req.headers["authorization"] == "Bearer sk-test"
    b = cap.body
    assert b["model"] == "gpt-test" and b["max_completion_tokens"] == 700 and b["tools"] == TOOLS
    assert "think" not in b and "stream_options" not in b
    assert b["messages"][0] == {"role": "system", "content": "你是 Sentinel AI 的維修助理"}
    calls = b["messages"][2]["tool_calls"]
    assert [json.loads(x["function"]["arguments"]) for x in calls] == [{"phase": "verify"}, {"phase": "repair"}]
    assert [m["tool_call_id"] for m in b["messages"][3:]] == [x["id"] for x in calls]
    assert all("tool_name" not in m for m in b["messages"])
    assert res.content == "" and res.tokens == 120 and res.model == "gpt-test"
    assert res.tool_calls == [{"id": "call_a", "function": {"name": "get_sop", "arguments": {"phase": "inspect"}}}]


async def test_openai_stream_text_and_tool_calls():
    def chunk(delta: dict | None, usage: dict | None = None) -> dict:
        return {"id": "c", "model": "gpt-test", "choices": [] if delta is None else [{"index": 0, "delta": delta}],
                **({"usage": usage} if usage else {})}

    events = [chunk({"role": "assistant", "content": ""}), chunk({"content": "目前先驗證"}), chunk({"content": "滾輪。"}),
              chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                     "function": {"name": "get_sop", "arguments": ""}}]}),
              chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{\"phase\":"}}]}),
              chunk({"tool_calls": [{"index": 0, "function": {"arguments": "\"verify\"}"}}]}),
              chunk(None, {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60})]
    cap = Capture(lambda b: httpx.Response(200, content=sse(events, done=True), headers={"content-type": "text/event-stream"}))
    parts, res = await collect(openai(cap), tools=TOOLS)
    assert cap.body["stream"] is True and cap.body["stream_options"] == {"include_usage": True}
    assert parts == ["目前先驗證", "滾輪。"] and res.content == "目前先驗證滾輪。"
    assert res.tool_calls == [{"id": "call_1", "function": {"name": "get_sop", "arguments": {"phase": "verify"}}}]
    assert res.tokens == 60 and res.ttft_ms is not None


# ── 設定與切換 ──────────────────────────────────────────────────
def test_provider_config_and_factory():
    cfg = LlmCfg()
    assert cfg.provider == "ollama" and cfg.endpoint() == ("https://ollama.com/api/chat", "deepseek-v4.1-flash")
    assert LlmCfg(provider="anthropic").endpoint() == ("https://api.anthropic.com/v1/messages", "claude-sonnet-5")
    assert LlmCfg(provider="openai", openai={"url": "https://api.openai.com/v1/chat/completions", "model": "gpt-x"}
                  ).endpoint()[1] == "gpt-x"
    secrets = Secrets(_env_file=None, ollama_api_key="o", anthropic_api_key="a", openai_api_key="g")
    assert Settings(secrets=secrets).llm_key() == "o"
    assert Settings(llm=LlmCfg(provider="anthropic"), secrets=secrets).llm_key() == "a"
    assert Settings(llm=LlmCfg(provider="openai"), secrets=secrets).llm_key() == "g"
    assert type(make_llm_client("ollama", api_key="k", url="u", model="m")) is LlmClient
    assert isinstance(make_llm_client("anthropic", api_key="k", url="u", model="m"), AnthropicClient)
    with pytest.raises(ValueError, match="不支援"):
        make_llm_client("gemini", api_key="k", url="u", model="m")


async def test_runtime_uses_configured_provider(settings, clock):
    from sentinel.runtime import Runtime

    settings.llm.provider = "anthropic"
    rt = Runtime(settings, clock=clock, llm_key="k", use_vector_store=False)
    assert isinstance(rt.llm, AnthropicClient) and rt.llm.enabled
    assert (rt.llm.url, rt.llm.model) == ("https://api.anthropic.com/v1/messages", "claude-sonnet-5")
    await rt.stop()
    settings.llm.provider = "openai"                               # 沒填 openai.model → 停用，不會亂打 API
    rt = Runtime(settings, clock=clock, llm_key="k", use_vector_store=False)
    assert rt.llm.enabled is False
    await rt.stop()
