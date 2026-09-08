"""Agent Anthropic 凭证内的多 key 池：选 key、日限额、429 冷却。

状态 JSON 可落库到 ``agent_anthropic_credentials.key_pool_state``；本模块不碰 DB。
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_DAILY_LIMIT = 2000
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_SOFT_COOLDOWN = timedelta(seconds=60)


@dataclass(frozen=True)
class PoolKey:
    key: str
    daily_limit: int = DEFAULT_DAILY_LIMIT


class KeyPoolEmptyError(RuntimeError):
    """池中无可用 key（全冷却或日限额耗尽）。"""


def fingerprint(key: str) -> str:
    """短指纹：日志与 state 字典键用，不落明文。"""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return digest[:16]


def parse_api_keys(raw: str | list[Any] | None, *, fallback_api_key: str | None = None) -> list[PoolKey]:
    """解析凭证上的 api_keys JSON / 列表；空则回退单 api_key。"""
    items: list[Any]
    if raw is None or raw == "":
        items = []
    elif isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            items = []
        else:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("api_keys must be valid JSON") from exc
            if not isinstance(parsed, list):
                raise ValueError("api_keys must be a JSON array")
            items = parsed
    else:
        raise ValueError("api_keys has unsupported type")

    out: list[PoolKey] = []
    for item in items:
        if isinstance(item, str):
            key = item.strip()
            if key:
                out.append(PoolKey(key=key))
            continue
        if isinstance(item, dict):
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            limit_raw = item.get("daily_limit", DEFAULT_DAILY_LIMIT)
            try:
                limit = int(limit_raw)
            except (TypeError, ValueError):
                limit = DEFAULT_DAILY_LIMIT
            if limit < 1:
                limit = DEFAULT_DAILY_LIMIT
            out.append(PoolKey(key=key, daily_limit=limit))
            continue
        raise ValueError("api_keys entries must be strings or {key, daily_limit?} objects")

    if not out and fallback_api_key and fallback_api_key.strip():
        out.append(PoolKey(key=fallback_api_key.strip()))
    return out


def serialize_api_keys(keys: list[PoolKey]) -> str:
    return json.dumps([{"key": k.key, "daily_limit": k.daily_limit} for k in keys], ensure_ascii=False)


def coerce_keys_from_text(text: str) -> list[PoolKey]:
    """把多行 / 逗号分隔的 key 文本解析为池（支持 ``key:daily_limit``）。"""
    items: list[PoolKey] = []
    for part in text.replace(",", "\n").splitlines():
        line = part.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, limit_s = line.rsplit(":", 1)
            key = key.strip()
            if not key:
                continue
            try:
                limit = int(limit_s.strip())
            except ValueError:
                items.append(PoolKey(key=line))
                continue
            items.append(PoolKey(key=key, daily_limit=limit if limit >= 1 else DEFAULT_DAILY_LIMIT))
        else:
            items.append(PoolKey(key=line))
    return items


def load_state(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if raw is None or raw == "":
        return {"timezone": DEFAULT_TIMEZONE, "keys": {}}
    if isinstance(raw, dict):
        state = dict(raw)
    else:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"timezone": DEFAULT_TIMEZONE, "keys": {}}
        if not isinstance(parsed, dict):
            return {"timezone": DEFAULT_TIMEZONE, "keys": {}}
        state = parsed
    state.setdefault("timezone", DEFAULT_TIMEZONE)
    keys = state.get("keys")
    if not isinstance(keys, dict):
        state["keys"] = {}
    return state


def dump_state(state: dict[str, Any]) -> str:
    return json.dumps(state, ensure_ascii=False)


def _today(tz_name: str, now: datetime) -> str:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        try:
            tz = ZoneInfo(DEFAULT_TIMEZONE)
        except Exception:
            tz = UTC
    local = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=UTC).astimezone(tz)
    return local.date().isoformat()


def _ensure_key_stats(state: dict[str, Any], fp: str, *, today: str) -> dict[str, Any]:
    keys = state.setdefault("keys", {})
    assert isinstance(keys, dict)
    entry = keys.get(fp)
    if not isinstance(entry, dict):
        entry = {"daily_count": 0, "blocked_until": None, "reset_day": today}
        keys[fp] = entry
    if entry.get("reset_day") != today:
        entry["daily_count"] = 0
        entry["reset_day"] = today
        # 日切时清掉「禁到次日」类阻塞；短冷却若仍在未来则保留
        blocked = entry.get("blocked_until")
        if isinstance(blocked, str) and blocked:
            try:
                until = datetime.fromisoformat(blocked.replace("Z", "+00:00"))
            except ValueError:
                entry["blocked_until"] = None
            else:
                # 若 blocked 落在今天之前的日历意图，已由 reset 覆盖；保留未来时间戳
                if until.tzinfo is None:
                    until = until.replace(tzinfo=UTC)
                if until <= datetime.now(UTC):
                    entry["blocked_until"] = None
    return entry


def select_key(
    pool: list[PoolKey],
    state: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """按剩余日额度加权选一把可用 key，并预占 1 次用量。"""
    if not pool:
        raise KeyPoolEmptyError("api key pool is empty")
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    tz_name = str(state.get("timezone") or DEFAULT_TIMEZONE)
    today = _today(tz_name, now)
    candidates: list[tuple[PoolKey, float]] = []
    for item in pool:
        fp = fingerprint(item.key)
        stats = _ensure_key_stats(state, fp, today=today)
        blocked_raw = stats.get("blocked_until")
        if isinstance(blocked_raw, str) and blocked_raw:
            try:
                until = datetime.fromisoformat(blocked_raw.replace("Z", "+00:00"))
                if until.tzinfo is None:
                    until = until.replace(tzinfo=UTC)
                if until > now:
                    continue
            except ValueError:
                stats["blocked_until"] = None
        count = int(stats.get("daily_count") or 0)
        if count >= item.daily_limit:
            continue
        remaining_pct = (item.daily_limit - count) / item.daily_limit
        candidates.append((item, remaining_pct))
    if not candidates:
        raise KeyPoolEmptyError("all api keys are rate limited or exhausted")
    candidates.sort(key=lambda pair: pair[1], reverse=True)
    top = max(1, len(candidates) // 2)
    chosen = random.choice([c[0] for c in candidates[:top]])
    fp = fingerprint(chosen.key)
    stats = _ensure_key_stats(state, fp, today=today)
    stats["daily_count"] = int(stats.get("daily_count") or 0) + 1
    return chosen.key, state


def mark_rate_limited(
    state: dict[str, Any],
    key: str,
    *,
    until: datetime | None = None,
    daily: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """标记 key 冷却。``daily=True`` 禁到时区次日 00:00。"""
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    tz_name = str(state.get("timezone") or DEFAULT_TIMEZONE)
    today = _today(tz_name, now)
    fp = fingerprint(key)
    stats = _ensure_key_stats(state, fp, today=today)
    if daily:
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            try:
                tz = ZoneInfo(DEFAULT_TIMEZONE)
            except Exception:
                tz = UTC
        local = now.astimezone(tz)
        next_midnight = (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(UTC)
        stats["blocked_until"] = next_midnight.isoformat().replace("+00:00", "Z")
        stats["daily_count"] = 10**9  # 同步视为日限额耗尽
    else:
        target = until or (now + DEFAULT_SOFT_COOLDOWN)
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        stats["blocked_until"] = target.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return state
