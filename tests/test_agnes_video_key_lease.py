"""Agnes video key-lease + submit throttle unit tests."""

from __future__ import annotations

import pytest

from lib.agnes_video_key_lease import (
    AGNES_VIDEO_MIN_INTERVAL_SECONDS,
    AgnesVideoKeyLeaseTable,
    AgnesVideoSubmitThrottle,
    get_leased_api_key,
    parse_agnes_api_key_pool,
    reset_leased_api_key,
    set_leased_api_key,
)

pytestmark = pytest.mark.unit


def test_parse_pool_prefers_api_keys_json() -> None:
    keys = parse_agnes_api_key_pool(
        api_key="primary",
        api_keys_json='[{"key":"k1"},{"key":"k2"},{"key":"k1"}]',
    )
    assert keys == ["k1", "k2"]


def test_parse_pool_falls_back_to_single_api_key() -> None:
    assert parse_agnes_api_key_pool(api_key=" only ", api_keys_json=None) == ["only"]


def test_lease_table_acquires_releases_and_recycles() -> None:
    table = AgnesVideoKeyLeaseTable()
    pool = ["a", "b"]
    assert table.try_acquire("t1", pool) == "a"
    assert table.try_acquire("t2", pool) == "b"
    assert table.try_acquire("t3", pool) is None
    table.release("t1")
    assert table.try_acquire("t3", pool) == "a"
    table.release("t2")
    table.release("t3")
    assert table.leased_count() == 0


def test_contextvar_leased_key_roundtrip() -> None:
    assert get_leased_api_key() is None
    token = set_leased_api_key("secret")
    try:
        assert get_leased_api_key() == "secret"
    finally:
        reset_leased_api_key(token)
    assert get_leased_api_key() is None


@pytest.mark.asyncio
async def test_submit_throttle_delays_second_attempt_same_key() -> None:
    clock = {"t": 1000.0}
    slept: list[float] = []

    def _clock() -> float:
        return clock["t"]

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["t"] += seconds

    throttle = AgnesVideoSubmitThrottle(
        min_interval_seconds=AGNES_VIDEO_MIN_INTERVAL_SECONDS,
        _clock=_clock,
        _sleep=_sleep,
    )
    assert await throttle.wait_turn("sk-a") == 0.0
    throttle.mark_attempt("sk-a")
    # 距上次仅 10s → 应再等 50s
    clock["t"] = 1010.0
    waited = await throttle.wait_turn("sk-a")
    assert waited == 50.0
    assert slept == [50.0]
    # 不同 key 不互斥
    slept.clear()
    assert await throttle.wait_turn("sk-b") == 0.0
    assert slept == []


@pytest.mark.asyncio
async def test_submit_throttle_ready_after_interval() -> None:
    clock = {"t": 0.0}

    def _clock() -> float:
        return clock["t"]

    async def _sleep(seconds: float) -> None:
        raise AssertionError(f"should not sleep, got {seconds}")

    throttle = AgnesVideoSubmitThrottle(
        min_interval_seconds=60.0,
        _clock=_clock,
        _sleep=_sleep,
    )
    throttle.mark_attempt("sk-a")
    clock["t"] = 60.0
    assert await throttle.wait_turn("sk-a") == 0.0
