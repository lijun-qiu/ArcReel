"""Tests for local IndexTTS control router (host agent forwarding)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers import local_indextts

pytestmark = pytest.mark.unit


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("INDEXTTS_CONTROL_BASE_URL", "http://control.test:10004")
    app = FastAPI()
    app.include_router(local_indextts.router, prefix="/api/v1")
    return TestClient(app)


def _ok_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("GET", "http://control.test/"))


def test_status_when_control_down(client: TestClient) -> None:
    with patch("server.routers.local_indextts.httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        instance.request.side_effect = httpx.ConnectError("boom")
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        mock_cls.return_value = instance

        res = client.get("/api/v1/local-services/indextts/status")
        assert res.status_code == 200
        body = res.json()
        assert body["running"] is False
        assert body["control_reachable"] is False
        assert "host control" in body["error"].lower() or "control" in body["error"].lower()


def test_start_forwards_to_control(client: TestClient) -> None:
    with patch("server.routers.local_indextts.httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        instance.request = AsyncMock(
            return_value=_ok_response({"ok": True, "running": True, "started": True, "root": "C:/x"})
        )
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        mock_cls.return_value = instance

        res = client.post("/api/v1/local-services/indextts/start")
        assert res.status_code == 200
        body = res.json()
        assert body["running"] is True
        assert body["started"] is True
        assert body["control_reachable"] is True
        instance.request.assert_awaited()
        args = instance.request.await_args
        assert args.args[0] == "POST"
        assert args.args[1].endswith("/start")


def test_unload_forwards_to_control(client: TestClient) -> None:
    with patch("server.routers.local_indextts.httpx.AsyncClient") as mock_cls:
        instance = AsyncMock()
        instance.request = AsyncMock(
            return_value=_ok_response({"ok": True, "killed": 2, "running": False, "available": False})
        )
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        mock_cls.return_value = instance

        res = client.post("/api/v1/local-services/indextts/unload")
        assert res.status_code == 200
        body = res.json()
        assert body["killed"] == 2
        assert body["running"] is False
