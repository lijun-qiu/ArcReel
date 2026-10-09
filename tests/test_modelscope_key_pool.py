"""魔搭 ModelScope key 池：解析与手动选用。"""

from __future__ import annotations

import pytest

from lib.agent_key_pool import fingerprint
from lib.modelscope_key_pool import (
    ModelScopePoolEntry,
    coerce_modelscope_keys_from_text,
    parse_modelscope_api_keys,
    resolve_active_modelscope_key,
    select_modelscope_key_by_fingerprint,
    serialize_modelscope_api_keys,
)


@pytest.mark.unit
def test_parse_and_serialize_roundtrip() -> None:
    raw = '[{"key":"ms-aaa","remaining":10,"label":"K1"},{"key":"ms-bbb","remaining":0}]'
    pool = parse_modelscope_api_keys(raw)
    assert len(pool) == 2
    assert pool[0].label == "K1"
    # remaining 字段被忽略，不再落库
    again = parse_modelscope_api_keys(serialize_modelscope_api_keys(pool))
    assert again == pool
    assert "remaining" not in serialize_modelscope_api_keys(pool)


@pytest.mark.unit
def test_coerce_from_text_formats() -> None:
    pool = coerce_modelscope_keys_from_text("ms-one\nms-two:80\nK9|ms-three|5\n")
    assert [e.key for e in pool] == ["ms-one", "ms-two", "ms-three"]
    assert pool[0].label == "K1"
    assert pool[2].label == "K9"


@pytest.mark.unit
def test_resolve_active_prefers_api_key() -> None:
    pool_json = serialize_modelscope_api_keys(
        [ModelScopePoolEntry(key="ms-a"), ModelScopePoolEntry(key="ms-b")]
    )
    assert resolve_active_modelscope_key(api_key="ms-b", api_keys_json=pool_json) == "ms-b"
    assert resolve_active_modelscope_key(api_key=None, api_keys_json=pool_json) == "ms-a"


@pytest.mark.unit
def test_select_by_fingerprint() -> None:
    pool = [ModelScopePoolEntry(key="ms-alpha"), ModelScopePoolEntry(key="ms-beta")]
    entry = select_modelscope_key_by_fingerprint(pool, fingerprint("ms-beta"))
    assert entry is not None
    assert entry.key == "ms-beta"
    assert select_modelscope_key_by_fingerprint(pool, "missing") is None
