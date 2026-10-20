"""Minimal Ollama Cloud client for DeepSeek V4.1 Flash (content drafting only).

Loads OLLAMA_API_KEY from the repo-root .env. Never prints the key.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
URL = "https://ollama.com/api/chat"
MODEL = "deepseek-v4.1-flash"


def _load_key() -> str:
    key = os.environ.get("OLLAMA_API_KEY")
    if key:
        return key
    env = ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("OLLAMA_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("OLLAMA_API_KEY not found")


def chat_json(system: str, user: str, schema: dict | str = "json", *, retries: int = 3,
              timeout: float = 240.0, temperature: float = 0.7, seed: int | None = None) -> dict:
    headers = {"Authorization": f"Bearer {_load_key()}"}
    body = {
        "model": MODEL,
        "stream": False,
        "format": schema,
        "options": {"temperature": temperature, **({"seed": seed} if seed is not None else {})},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    last = None
    for i in range(retries):
        try:
            r = httpx.post(URL, json=body, headers=headers, timeout=timeout)
            r.raise_for_status()
            content = r.json()["message"]["content"]
            return json.loads(content)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"LLM call failed: {type(last).__name__}: {str(last)[:200]}")


if __name__ == "__main__":
    print(chat_json("只輸出 JSON。", '回傳 {"ok": true, "msg": "測試"}'))
