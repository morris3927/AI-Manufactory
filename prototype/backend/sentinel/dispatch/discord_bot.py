"""Discord bot（NT-2，M2）：接收「接單／轉派」按鈕事件後呼叫 dispatch 服務。

只有設定 DISCORD_BOT_TOKEN 且安裝 discord.py（pip install -e '.[discord]'）時才會啟動。
Discord 帳號 ID 與人員的對應寫在 roster.csv 的 discord_id 欄位。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlmodel import select

from ..db.models import Person

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)


async def run_bot(rt: "Runtime", token: str) -> None:
    try:
        import discord  # 延遲載入（選配套件）
    except ImportError:
        log.info("未安裝 discord.py，略過 Discord bot")
        return

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_interaction(interaction: "discord.Interaction") -> None:  # type: ignore[name-defined]
        custom_id = (interaction.data or {}).get("custom_id", "")
        if ":" not in custom_id:
            return
        action, wo_id = custom_id.split(":", 1)
        person = rt.db.first(select(Person).where(Person.discord_id == str(interaction.user.id)))
        if person is None:
            await interaction.response.send_message("找不到你的人員資料（觀眾頻道只能看，不能按）", ephemeral=True)
            return
        try:
            if action == "ack":
                await rt.workorders.ack(wo_id, person.id)
                await interaction.response.send_message(f"{person.name} 已接單 {wo_id}")
            else:
                await interaction.response.send_message("轉派請到 Kanban 操作", ephemeral=True)
        except Exception as e:  # 狀態不允許等
            await interaction.response.send_message(f"無法操作：{e}", ephemeral=True)

    await client.start(token)
