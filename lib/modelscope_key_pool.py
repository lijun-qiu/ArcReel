"""魔搭 ModelScope 文本 key 池：手动选用（不做自动轮换、不做本地 remaining 计数）。

存于 ``provider_credential.api_keys``（JSON 列表）；``api_key`` 为当前选用明文。
旧数据里的 ``remaining`` 字段解析时忽略。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from lib.agent_key_pool import fingerprint


@dataclass(frozen=True)
class ModelScopePoolEntry:
    key: str
    label: str | None = None


def parse_modelscope_api_keys(
    raw: str | list[Any] | None,
    *,
    fallback_api_key: str | None = None,
) -> list[ModelScopePoolEntry]:
    """解析魔搭 key 池；空池且无条目时返回空列表（不把单 api_key 合成池）。"""
    del fallback_api_key  # 保留签名兼容，单 key 不进池
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

    out: list[ModelScopePoolEntry] = []
    for item in items:
        if isinstance(item, str):
            key = item.strip()
            if key:
                out.append(ModelScopePoolEntry(key=key))
            continue
        if isinstance(item, dict):
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            label_raw = item.get("label")
            label = str(label_raw).strip() if isinstance(label_raw, str) and label_raw.strip() else None
            out.append(ModelScopePoolEntry(key=key, label=label))
            continue
        raise ValueError("api_keys entries must be strings or {key, label?} objects")
    return out


def serialize_modelscope_api_keys(entries: list[ModelScopePoolEntry]) -> str:
    payload: list[dict[str, Any]] = []
    for entry in entries:
        row: dict[str, Any] = {"key": entry.key}
        if entry.label:
            row["label"] = entry.label
        payload.append(row)
    return json.dumps(payload, ensure_ascii=False)


def coerce_modelscope_keys_from_text(text: str) -> list[ModelScopePoolEntry]:
    """多行粘贴：``key`` / ``label|key``（兼容旧 ``key:n`` / ``label|key|n``，忽略次数）。"""
    items: list[ModelScopePoolEntry] = []
    for part in text.replace(",", "\n").splitlines():
        line = part.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            bits = [b.strip() for b in line.split("|")]
            if len(bits) >= 2 and bits[1]:
                label = bits[0] or None
                key = bits[1]
                items.append(ModelScopePoolEntry(key=key, label=label))
                continue
        if ":" in line:
            key, maybe_rem = line.rsplit(":", 1)
            key = key.strip()
            if key and maybe_rem.strip().isdigit():
                items.append(ModelScopePoolEntry(key=key))
                continue
        items.append(ModelScopePoolEntry(key=line))
    labeled: list[ModelScopePoolEntry] = []
    for i, entry in enumerate(items):
        if entry.label:
            labeled.append(entry)
        else:
            labeled.append(ModelScopePoolEntry(key=entry.key, label=f"K{i + 1}"))
    return labeled


def resolve_active_modelscope_key(
    *,
    api_key: str | None,
    api_keys_json: str | None,
) -> str | None:
    """当前选用 key：优先 ``api_key``；池存在且 api_key 空时回退首条。"""
    active = (api_key or "").strip()
    if active:
        return active
    pool = parse_modelscope_api_keys(api_keys_json)
    return pool[0].key if pool else None


def find_pool_entry(entries: list[ModelScopePoolEntry], key: str) -> ModelScopePoolEntry | None:
    trimmed = key.strip()
    if not trimmed:
        return None
    for entry in entries:
        if entry.key == trimmed:
            return entry
    return None


def select_modelscope_key_by_fingerprint(
    entries: list[ModelScopePoolEntry],
    fp: str,
) -> ModelScopePoolEntry | None:
    target = (fp or "").strip()
    if not target:
        return None
    for entry in entries:
        if fingerprint(entry.key) == target:
            return entry
    return None
