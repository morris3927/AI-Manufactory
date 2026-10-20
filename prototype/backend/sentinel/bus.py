"""事件匯流（08_系統設計.md §4.3）。

- 每個訂閱者一個 asyncio.Queue。
- 佇列滿時：`frames` 丟棄最舊的一幀；其他 topic 不能丟，記錄錯誤後等待（背壓）。
  例外：只供畫面顯示的訂閱（WebSocket，lossy=True）一律丟最舊，避免前端太慢拖住處理流程。
- 除了 `frames`，所有事件發布時同步寫入 `event` 表（persist 回呼）。
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from .schemas.events import Event, Topic

log = logging.getLogger(__name__)

Handler = Callable[[Event], Awaitable[None]]


@dataclass
class Subscription:
    topic: str
    name: str
    queue: asyncio.Queue[Event]
    consumed: bool = False          # 由 consume() 建立的訂閱，drain() 會等它處理完
    lossy: bool = False             # 顯示用訂閱（WebSocket）：滿了丟最舊，不能拖慢處理流程
    dropped: int = 0
    task: asyncio.Task | None = field(default=None, repr=False)


class EventBus:
    def __init__(self, persist: Callable[[Event], None] | None = None):
        self._subs: dict[str, list[Subscription]] = defaultdict(list)
        self._persist = persist
        self._pending: set[asyncio.Task] = set()
        self.published = 0

    # ── 發布／訂閱 ─────────────────────────────────────────────
    async def publish(self, event: Event) -> None:
        if event.topic != Topic.FRAMES and self._persist:
            try:
                self._persist(event)
            except Exception:  # 寫入失敗不能擋住即時流程
                log.exception("事件寫入資料庫失敗：%s", event.type)
        self.published += 1
        for sub in list(self._subs.get(event.topic, ())):
            q = sub.queue
            if q.full():
                if event.topic == Topic.FRAMES or sub.lossy:
                    try:
                        q.get_nowait()
                        q.task_done()
                    except asyncio.QueueEmpty:
                        pass
                    sub.dropped += 1
                else:
                    log.error("訂閱者 %s 的 %s 佇列已滿，事件不能丟棄，等待處理", sub.name, event.topic)
                    await q.put(event)
                    continue
            q.put_nowait(event)

    def subscribe(self, topic: str, name: str, maxsize: int = 1000, *, lossy: bool = False) -> asyncio.Queue[Event]:
        sub = Subscription(topic=str(topic), name=name, queue=asyncio.Queue(maxsize=maxsize), lossy=lossy)
        self._subs[sub.topic].append(sub)
        return sub.queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        for subs in self._subs.values():
            for sub in list(subs):
                if sub.queue is queue:
                    if sub.task:
                        sub.task.cancel()
                    subs.remove(sub)

    def consume(self, topic: str, name: str, handler: Handler, maxsize: int = 1000) -> asyncio.Task:
        """訂閱並啟動背景消費者；handler 例外只記錄，不會中斷消費。"""
        queue = self.subscribe(topic, name, maxsize)
        sub = next(s for s in self._subs[str(topic)] if s.queue is queue)
        sub.consumed = True

        async def _loop() -> None:
            while True:
                evt = await queue.get()
                try:
                    await handler(evt)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("%s 處理 %s/%s 失敗", name, evt.topic, evt.type)
                finally:
                    queue.task_done()

        sub.task = asyncio.create_task(_loop(), name=f"bus:{name}:{topic}")
        return sub.task

    # ── 背景工作追蹤（Jev／LLM 非同步呼叫）─────────────────────
    def track(self, coro: Coroutine[Any, Any, Any], name: str | None = None) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._pending.add(task)

        def _done(t: asyncio.Task) -> None:
            self._pending.discard(t)
            if not t.cancelled() and t.exception():
                log.error("背景工作 %s 失敗：%r", t.get_name(), t.exception())

        task.add_done_callback(_done)
        return task

    async def drain(self, timeout: float = 30.0) -> None:
        """等所有消費者佇列與背景工作都處理完（逐步重播與測試用）。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            await asyncio.sleep(0)
            busy = any(
                s.consumed and s.queue._unfinished_tasks  # type: ignore[attr-defined]
                for subs in self._subs.values() for s in subs
            )
            if not busy and not self._pending:
                return
            if loop.time() > deadline:
                raise TimeoutError("bus.drain 逾時")
            if self._pending:
                await asyncio.wait(set(self._pending), timeout=0.05)
            else:
                await asyncio.sleep(0.001)

    async def close(self) -> None:
        tasks = [s.task for subs in self._subs.values() for s in subs if s.task]
        tasks += list(self._pending)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._subs.clear()

    def stats(self) -> dict[str, Any]:
        return {
            "published": self.published,
            "subscribers": {
                f"{s.name}:{t}": {"qsize": s.queue.qsize(), "dropped": s.dropped}
                for t, subs in self._subs.items() for s in subs
            },
        }
