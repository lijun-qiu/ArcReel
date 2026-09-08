"""AnthropicKeyRotator SSE 透传与鉴权。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.db.models.agent_credential import AgentAnthropicCredential
from server.agent_runtime.anthropic_key_rotator import AnthropicKeyRotator

pytestmark = pytest.mark.unit


class _FakeStreamResponse:
    def __init__(self, *, status_code: int, headers: dict[str, str], chunks: list[bytes]) -> None:
        self.status_code = status_code
        self.headers = headers
        self._chunks = chunks

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def aread(self) -> bytes:
        return b"".join(self._chunks)


class _FakeStreamCM:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeStreamResponse:
        return self._response

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_rotator_passthrough_sse_bytes() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add(
            AgentAnthropicCredential(
                user_id="default",
                preset_id="__custom__",
                display_name="pool",
                base_url="https://upstream.example/anthropic",
                api_key="sk-a",
                api_keys=json.dumps([{"key": "sk-a"}, {"key": "sk-b"}]),
                model="m",
                is_active=True,
            )
        )
        await session.commit()

    rotator = AnthropicKeyRotator()
    rotator.bind_session_factory(factory)
    await rotator.start()

    captured: dict[str, Any] = {}

    def fake_stream(method, url, headers=None, content=None, params=None):  # noqa: ARG001
        captured["url"] = str(url)
        captured["x_api_key"] = (headers or {}).get("x-api-key")
        return _FakeStreamCM(
            _FakeStreamResponse(
                status_code=200,
                headers={"content-type": "text/event-stream"},
                chunks=[
                    b"event: message_start\ndata: {\"type\":\"message_start\"}\n\n",
                    b"data: [DONE]\n\n",
                ],
            )
        )

    assert rotator._http is not None
    rotator._http.stream = fake_stream  # type: ignore[method-assign]

    try:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{rotator.base_url}/v1/messages",
                headers={"x-api-key": rotator.proxy_token, "content-type": "application/json"},
                content=b'{"model":"m","messages":[],"max_tokens":1,"stream":true}',
            ) as resp:
                assert resp.status_code == 200
                body = b"".join([chunk async for chunk in resp.aiter_bytes()])
        assert b"message_start" in body
        assert b"[DONE]" in body
        assert captured["url"].startswith("https://upstream.example/anthropic/")
        assert captured["x_api_key"] in {"sk-a", "sk-b"}
    finally:
        await rotator.stop()
        await engine.dispose()


@pytest.mark.asyncio
async def test_rotator_rejects_bad_token() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    rotator = AnthropicKeyRotator()
    rotator.bind_session_factory(factory)
    await rotator.start()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{rotator.base_url}/v1/messages",
                headers={"x-api-key": "wrong", "content-type": "application/json"},
                content=b"{}",
            )
        assert resp.status_code == 401
    finally:
        await rotator.stop()
        await engine.dispose()
