"""派工通知（NT-1、NT-3）。

- ConsoleNotifier：沒有 Discord 設定時的預設；寫到 log，並保留在記憶體（測試與前端用）。
- DiscordNotifier：只有設定 DISCORD_BOT_TOKEN 且 config.yaml 有頻道 ID 時才啟用（REST API 發送 embed＋按鈕）。
  按鈕事件需要 bot 常駐（discord_bot.py，M2）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from ..ids import new_id

log = logging.getLogger(__name__)

SEV_COLOR = {"S1": 0x9CA3AF, "S2": 0xF59E0B, "S3": 0xEF4444}
CAT_LABEL = {"safety": "工安", "equipment": "設備", "quality": "品質"}


@dataclass
class Message:
    channel: str
    title: str
    lines: list[str]
    severity: str
    workorder_id: str
    recipient_id: str | None = None
    buttons: list[str] = field(default_factory=lambda: ["接單", "轉派"])
    reply_to: str | None = None

    @property
    def text(self) -> str:
        return "\n".join([self.title, *self.lines])


class Notifier(Protocol):
    name: str

    async def send(self, msg: Message) -> str: ...


class ConsoleNotifier:
    name = "console"

    def __init__(self) -> None:
        self.sent: list[tuple[str, Message]] = []

    async def send(self, msg: Message) -> str:
        mid = f"console-{new_id()}"
        self.sent.append((mid, msg))
        prefix = f"↳ 回覆 {msg.reply_to}" if msg.reply_to else f"#{msg.channel}"
        log.info("[通知 %s] %s\n%s", prefix, msg.title, "\n".join(msg.lines))
        return mid


class DiscordNotifier:
    name = "discord"
    API = "https://discord.com/api/v10"

    def __init__(self, token: str, channels: dict[str, str], fallback: Notifier | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.channels = {k: v for k, v in channels.items() if v}
        self.fallback = fallback or ConsoleNotifier()
        self.http = httpx.AsyncClient(base_url=self.API, timeout=5.0, transport=transport,
                                      headers={"Authorization": f"Bot {token}"})

    async def send(self, msg: Message) -> str:
        channel_id = self.channels.get(msg.channel)
        if not channel_id:
            return await self.fallback.send(msg)
        body: dict[str, Any] = {"embeds": [{"title": msg.title, "description": "\n".join(msg.lines),
                                            "color": SEV_COLOR.get(msg.severity, 0x6B7280)}]}
        if msg.buttons and not msg.reply_to:
            body["components"] = [{"type": 1, "components": [
                {"type": 2, "style": 1 if i == 0 else 2, "label": b, "custom_id": f"{['ack', 'reassign'][i]}:{msg.workorder_id}"}
                for i, b in enumerate(msg.buttons[:2])]}]
        if msg.reply_to and not msg.reply_to.startswith("console-"):
            body["message_reference"] = {"message_id": msg.reply_to}
        try:
            r = await self.http.post(f"/channels/{channel_id}/messages", json=body)
            r.raise_for_status()
            return str(r.json().get("id"))
        except httpx.HTTPError as e:      # Discord 斷線：工單照建，改走備援
            log.warning("Discord 發送失敗（%s），改用 console", e)
            return await self.fallback.send(msg)


def make_notifier(token: str | None, channels: dict[str, str]) -> Notifier:
    if token and any(channels.values()):
        return DiscordNotifier(token, channels)
    return ConsoleNotifier()
