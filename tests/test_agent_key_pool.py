"""lib.agent_key_pool 单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lib.agent_key_pool import (
    KeyPoolEmptyError,
    PoolKey,
    coerce_keys_from_text,
    fingerprint,
    load_state,
    mark_rate_limited,
    parse_api_keys,
    select_key,
    serialize_api_keys,
)

pytestmark = pytest.mark.unit


def test_parse_api_keys_json_and_fallback() -> None:
    pool = parse_api_keys('[{"key":"sk-a","daily_limit":10}]')
    assert len(pool) == 1
    assert pool[0].key == "sk-a"
    assert pool[0].daily_limit == 10

    fallback = parse_api_keys(None, fallback_api_key="sk-only")
    assert [k.key for k in fallback] == ["sk-only"]


def test_coerce_keys_from_text_multiline_and_limits() -> None:
    pool = coerce_keys_from_text("sk-1\nsk-2:50\n# comment\nsk-3")
    assert [k.key for k in pool] == ["sk-1", "sk-2", "sk-3"]
    assert pool[1].daily_limit == 50


def test_serialize_roundtrip() -> None:
    keys = [PoolKey(key="sk-x", daily_limit=100)]
    raw = serialize_api_keys(keys)
    again = parse_api_keys(raw)
    assert again[0].key == "sk-x"
    assert again[0].daily_limit == 100


def test_select_key_preempts_usage_and_skips_blocked() -> None:
    pool = [PoolKey(key="sk-a"), PoolKey(key="sk-b")]
    state = load_state(None)
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    key1, state = select_key(pool, state, now=now)
    assert key1 in {"sk-a", "sk-b"}
    fp = fingerprint(key1)
    assert state["keys"][fp]["daily_count"] == 1

    mark_rate_limited(state, key1, until=now + timedelta(hours=1), now=now)
    key2, state = select_key(pool, state, now=now)
    assert key2 != key1


def test_select_key_empty_pool_raises() -> None:
    with pytest.raises(KeyPoolEmptyError):
        select_key([], load_state(None))
