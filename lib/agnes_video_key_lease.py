"""Agnes 视频：按 API key 租约实现「一 key 一路」，用完回收；创建 POST 按 key 限速。

触发条件（见 ``GenerationWorker``）：
- 凡 Agnes ``video`` / ``reference_video`` 且凭证池非空，一律走 key 租约
  （单 key 时并发=1，避免 CapacityTable 默认多槽挤同一把钥匙）；
- 手动批量 payload ``use_agnes_key_pool=true`` 仍可显式标记（与自动租约语义一致）。

创建 POST 另有进程内限速：同一 key 两次提交间隔 ≥ ``AGNES_VIDEO_MIN_INTERVAL_SECONDS``
（对齐 LocalMiniDrama ``AGNES_VIDEO_MIN_INTERVAL_MS``），只约束创建、不挡轮询。

进程内台账：worker 重启后租约与限速时钟清空，未完成任务需重新 claim 再租。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from threading import Lock

from lib.agent_key_pool import fingerprint

# 执行期注入：assemble_backend 读到后覆盖 Agnes credentials["api_key"]
_leased_api_key: ContextVar[str | None] = ContextVar("agnes_leased_api_key", default=None)

# 与 LocalMiniDrama backend-node AGNES_VIDEO_MIN_INTERVAL_MS=60_000 对齐。
AGNES_VIDEO_MIN_INTERVAL_SECONDS = 60.0


def set_leased_api_key(api_key: str | None) -> Token[str | None]:
    return _leased_api_key.set(api_key)


def reset_leased_api_key(token: Token[str | None]) -> None:
    _leased_api_key.reset(token)


def get_leased_api_key() -> str | None:
    return _leased_api_key.get()


def parse_agnes_api_key_pool(*, api_key: str | None, api_keys_json: str | None) -> list[str]:
    """激活凭证 → 去重后的 key 列表；``api_keys`` 优先，否则回退单 ``api_key``。"""
    from lib.agent_key_pool import parse_api_keys

    pool = parse_api_keys(api_keys_json, fallback_api_key=api_key)
    seen: set[str] = set()
    keys: list[str] = []
    for item in pool:
        raw = item.key.strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        keys.append(raw)
    return keys


@dataclass
class AgnesVideoKeyLeaseTable:
    """task_id → 明文 key；同一 key 同时只租给一个任务。"""

    _by_task: dict[str, str] = field(default_factory=dict)
    _by_fingerprint: dict[str, str] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def try_acquire(self, task_id: str, pool: list[str]) -> str | None:
        """从 *pool* 中租一把空闲 key；成功返回明文 key，池满返回 ``None``。"""
        if not pool:
            return None
        with self._lock:
            existing = self._by_task.get(task_id)
            if existing is not None:
                return existing
            for key in pool:
                fp = fingerprint(key)
                if fp in self._by_fingerprint:
                    continue
                self._by_task[task_id] = key
                self._by_fingerprint[fp] = task_id
                return key
            return None

    def release(self, task_id: str) -> None:
        with self._lock:
            key = self._by_task.pop(task_id, None)
            if key is None:
                return
            self._by_fingerprint.pop(fingerprint(key), None)

    def get(self, task_id: str) -> str | None:
        with self._lock:
            return self._by_task.get(task_id)

    def leased_count(self) -> int:
        with self._lock:
            return len(self._by_task)


@dataclass
class AgnesVideoSubmitThrottle:
    """同一 API key 创建 POST 的最小间隔（进程内）。

    ``clock`` / ``sleep`` 可注入，便于单测不撞墙钟。
    """

    min_interval_seconds: float = AGNES_VIDEO_MIN_INTERVAL_SECONDS
    _clock: Callable[[], float] = time.monotonic
    _sleep: Callable[[float], object] = asyncio.sleep
    _last_submit_at: dict[str, float] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    async def wait_turn(self, api_key: str) -> float:
        """等到该 key 距上次提交已满间隔；返回本轮累计等待秒数（未等则为 0）。"""
        raw = (api_key or "").strip()
        if not raw or self.min_interval_seconds <= 0:
            return 0.0
        fp = fingerprint(raw)
        total_wait = 0.0
        while True:
            with self._lock:
                last = self._last_submit_at.get(fp)
                now = self._clock()
                if last is None:
                    return total_wait
                wait = self.min_interval_seconds - (now - last)
                if wait <= 0:
                    return total_wait
            await self._sleep(wait)
            total_wait += wait
            # 醒来后再检查：可能有并发等同一 key，避免误判已就绪。

    def mark_attempt(self, api_key: str) -> None:
        """一次创建 POST 结束（成功或失败）后记账，启动下一轮间隔。"""
        raw = (api_key or "").strip()
        if not raw:
            return
        with self._lock:
            self._last_submit_at[fingerprint(raw)] = self._clock()

    def clear(self) -> None:
        with self._lock:
            self._last_submit_at.clear()


# 进程级单例：worker 内所有 Agnes 创建共享。
_submit_throttle = AgnesVideoSubmitThrottle()


def get_agnes_submit_throttle() -> AgnesVideoSubmitThrottle:
    return _submit_throttle


async def wait_agnes_submit_turn(api_key: str) -> float:
    return await _submit_throttle.wait_turn(api_key)


def mark_agnes_submit_attempt(api_key: str) -> None:
    _submit_throttle.mark_attempt(api_key)
