"""ArcReel 内嵌 Anthropic Messages 透传轮换。

SDK 只拿到 loopback base_url + 代理口令；本模块校验口令后从 DB active
凭证的 key 池选上游 key，按 Anthropic Messages 习惯设 ``x-api-key``，
响应体 / SSE **原样** ``aiter_bytes`` 转发，不解析 content_block。
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import socket
from dataclasses import dataclass
from typing import Any

import httpx
import uvicorn
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from lib.agent_key_pool import (
    KeyPoolEmptyError,
    PoolKey,
    dump_state,
    load_state,
    mark_rate_limited,
    parse_api_keys,
    select_key,
)
from lib.db.models.agent_credential import AgentAnthropicCredential

logger = logging.getLogger(__name__)

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-encoding",
    "content-length",
    "host",
}


@dataclass(frozen=True)
class _ActivePool:
    cred_id: int
    upstream_base_url: str
    pool: list[PoolKey]
    state: dict[str, Any]


class AnthropicKeyRotator:
    """进程内 loopback Anthropic 转发器（单例由模块级 ``get_rotator`` 持有）。"""

    def __init__(self) -> None:
        self.proxy_token: str = secrets.token_urlsafe(32)
        self.host: str = "127.0.0.1"
        self.port: int | None = None
        self._http: httpx.AsyncClient | None = None
        self._session_factory: Any = None
        self._serve_task: asyncio.Task[None] | None = None
        self._uvicorn: uvicorn.Server | None = None
        self._sock: socket.socket | None = None

    @property
    def base_url(self) -> str:
        if self.port is None:
            raise RuntimeError("anthropic key rotator is not running")
        return f"http://{self.host}:{self.port}"

    def bind_session_factory(self, factory: Any) -> None:
        self._session_factory = factory

    async def start(self) -> None:
        if self._uvicorn is not None:
            return
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=30.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        )
        app = Starlette(
            routes=[
                Route(
                    "/{path:path}",
                    self._proxy,
                    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                )
            ]
        )

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, 0))
        sock.listen(128)
        self.port = int(sock.getsockname()[1])
        self._sock = sock

        config = uvicorn.Config(app, log_level="warning", access_log=False)
        server = uvicorn.Server(config)
        self._uvicorn = server

        async def _serve() -> None:
            await server.serve(sockets=[sock])

        self._serve_task = asyncio.create_task(_serve(), name="anthropic-key-rotator")
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        if not server.started:
            await self.stop()
            raise RuntimeError("anthropic key rotator failed to start")
        logger.info("Anthropic key rotator listening on %s", self.base_url)

    async def stop(self) -> None:
        server = self._uvicorn
        self._uvicorn = None
        if server is not None:
            server.should_exit = True
        task = self._serve_task
        self._serve_task = None
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        self.port = None

    def _extract_client_token(self, request: Request) -> str | None:
        x_key = request.headers.get("x-api-key")
        if x_key:
            return x_key.strip()
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return None

    async def _load_active_pool(self) -> _ActivePool:
        if self._session_factory is None:
            raise RuntimeError("session factory not bound")
        async with self._session_factory() as session:
            stmt = select(AgentAnthropicCredential).where(AgentAnthropicCredential.is_active.is_(True))
            result = await session.execute(stmt)
            cred = result.scalar_one_or_none()
            if cred is None:
                raise RuntimeError("no active agent credential")
            pool = parse_api_keys(cred.api_keys, fallback_api_key=cred.api_key)
            state = load_state(cred.key_pool_state)
            return _ActivePool(
                cred_id=cred.id,
                upstream_base_url=(cred.base_url or "").rstrip("/"),
                pool=pool,
                state=state,
            )

    async def _persist_state(self, cred_id: int, state: dict[str, Any]) -> None:
        if self._session_factory is None:
            return
        try:
            async with self._session_factory() as session:
                stmt = select(AgentAnthropicCredential).where(AgentAnthropicCredential.id == cred_id)
                result = await session.execute(stmt)
                cred = result.scalar_one_or_none()
                if cred is None:
                    return
                cred.key_pool_state = dump_state(state)
                await session.commit()
        except Exception as exc:
            # 用量状态丢失可恢复；写库失败不应打断上游 SSE 透传。
            logger.warning("rotator failed to persist key pool state: %s", exc)

    async def _reload_state(self, cred_id: int) -> dict[str, Any]:
        if self._session_factory is None:
            return load_state(None)
        try:
            async with self._session_factory() as session:
                stmt = select(AgentAnthropicCredential).where(AgentAnthropicCredential.id == cred_id)
                result = await session.execute(stmt)
                cred = result.scalar_one_or_none()
                return load_state(None if cred is None else cred.key_pool_state)
        except Exception as exc:
            logger.warning("rotator failed to reload key pool state: %s", exc)
            return load_state(None)

    async def _proxy(self, request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(
                status_code=200,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": (
                        "Authorization, x-api-key, anthropic-version, anthropic-beta, Content-Type"
                    ),
                },
            )

        token = self._extract_client_token(request)
        if not token or not secrets.compare_digest(token, self.proxy_token):
            return Response(
                content='{"error":{"type":"authentication_error","message":"Invalid proxy token"}}',
                status_code=401,
                media_type="application/json",
            )

        if self._http is None:
            return Response(
                content='{"error":{"message":"rotator not ready"}}',
                status_code=503,
                media_type="application/json",
            )

        try:
            active = await self._load_active_pool()
        except Exception as exc:
            logger.warning("rotator failed to load credential pool: %s", exc)
            return Response(
                content='{"error":{"message":"no active credential pool"}}',
                status_code=503,
                media_type="application/json",
            )

        if not active.pool:
            return Response(
                content='{"error":{"message":"empty api key pool"}}',
                status_code=503,
                media_type="application/json",
            )

        state = active.state
        try:
            upstream_key, state = select_key(active.pool, state)
        except KeyPoolEmptyError:
            return Response(
                content='{"error":{"type":"rate_limit_error","message":"All API keys are rate limited."}}',
                status_code=429,
                media_type="application/json",
            )
        await self._persist_state(active.cred_id, state)

        upstream_root = active.upstream_base_url
        path = request.path_params.get("path") or ""
        # 魔搭本机注入代理的 UPSTREAM 已带 /v1；Anthropic SDK 再拼 /v1/messages 会双写。
        # 剥掉路径前缀 v1/，让代理收到 /messages → …/v1/messages。
        if _is_modelscope_openai_style_proxy(upstream_root) and (
            path == "v1" or path.startswith("v1/")
        ):
            path = path[3:] or ""
            if path.startswith("/"):
                path = path[1:]
        upstream_url = f"{upstream_root}/{path}" if path else upstream_root
        body = await request.body()

        is_streaming = False
        if request.method == "POST" and body:
            try:
                parsed = json.loads(body)
                is_streaming = bool(isinstance(parsed, dict) and parsed.get("stream"))
            except Exception:
                is_streaming = False
        if path.rstrip("/").endswith("messages") and "text/event-stream" in (request.headers.get("accept") or ""):
            is_streaming = True

        headers = {
            "content-type": request.headers.get("content-type") or "application/json",
            "x-api-key": upstream_key,
            "anthropic-version": request.headers.get("anthropic-version") or "2023-06-01",
            "accept": "text/event-stream"
            if is_streaming
            else (request.headers.get("accept") or "application/json"),
        }
        beta = request.headers.get("anthropic-beta")
        if beta:
            headers["anthropic-beta"] = beta

        params = dict(request.query_params)

        if is_streaming:
            return await self._stream_upstream(
                request.method,
                upstream_url,
                headers,
                body,
                params,
                cred_id=active.cred_id,
                upstream_key=upstream_key,
            )

        try:
            response = await self._http.request(
                request.method,
                upstream_url,
                headers=headers,
                content=body,
                params=params,
            )
        except httpx.RequestError as exc:
            logger.warning("rotator upstream request error: %s", exc)
            return Response(
                content='{"error":{"message":"Upstream unavailable"}}',
                status_code=502,
                media_type="application/json",
            )

        if response.status_code in (429, 402, 403):
            state = await self._reload_state(active.cred_id)
            daily = response.status_code == 429 and _looks_like_daily_quota(response.content)
            mark_rate_limited(state, upstream_key, daily=daily)
            await self._persist_state(active.cred_id, state)

        out_headers = {k: v for k, v in response.headers.items() if k.lower() not in _HOP_BY_HOP}
        out_headers["access-control-allow-origin"] = "*"
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=out_headers,
            media_type=response.headers.get("content-type"),
        )

    async def _stream_upstream(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        params: dict[str, str],
        *,
        cred_id: int,
        upstream_key: str,
    ) -> Response:
        assert self._http is not None
        try:
            cm = self._http.stream(method, url, headers=headers, content=body, params=params)
            response = await cm.__aenter__()
        except httpx.RequestError as exc:
            logger.warning("rotator stream connect error: %s", exc)
            return Response(
                content='{"error":{"message":"Upstream unavailable"}}',
                status_code=502,
                media_type="application/json",
            )

        status = response.status_code
        media = response.headers.get("content-type") or (
            "text/event-stream" if status == 200 else "application/json"
        )
        out_headers = {k: v for k, v in response.headers.items() if k.lower() not in _HOP_BY_HOP}
        out_headers["access-control-allow-origin"] = "*"
        if status == 200:
            out_headers["cache-control"] = "no-cache"

        if status in (429, 402, 403):
            raw = await response.aread()
            await cm.__aexit__(None, None, None)
            state = await self._reload_state(cred_id)
            daily = status == 429 and _looks_like_daily_quota(raw)
            mark_rate_limited(state, upstream_key, daily=daily)
            await self._persist_state(cred_id, state)
            return Response(content=raw, status_code=status, headers=out_headers, media_type=media)

        async def byte_stream():
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await cm.__aexit__(None, None, None)

        return StreamingResponse(byte_stream(), status_code=status, headers=out_headers, media_type=media)


def _is_modelscope_openai_style_proxy(base_url: str | None) -> bool:
    """本机魔搭 key 注入代理（OpenAI 兼容、UPSTREAM 已含 /v1）。"""
    if not base_url:
        return False
    lowered = base_url.lower().rstrip("/")
    return lowered.endswith(":10001") or "modelscope_proxy" in lowered


def _looks_like_daily_quota(body: bytes) -> bool:
    text = body.decode("utf-8", errors="ignore").lower()
    return any(
        needle in text
        for needle in (
            "free-models-per-day",
            "daily limit",
            "daily_limit",
            "quota exceeded",
            "insufficient quota",
            "今日",
            "天限额",
        )
    )


_rotator: AnthropicKeyRotator | None = None


def get_rotator() -> AnthropicKeyRotator:
    global _rotator
    if _rotator is None:
        _rotator = AnthropicKeyRotator()
    return _rotator
