"""Unit tests for Real-ESRGAN host upscale client helpers."""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_host_upscale_rejects_non_ok(monkeypatch):
    from server.services import realesrgan_upscale as mod

    class _Resp:
        status_code = 500

        def json(self):
            return {"ok": False, "error": "boom"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

        async def get(self, *a, **k):
            class _H:
                status_code = 200

                def json(self):
                    return {"ok": True, "running": True}

            return _H()

    async def _ensure():
        return {"ok": True, "running": True}

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(mod, "ensure_proxy", _ensure)
    with pytest.raises(RuntimeError, match="boom"):
        await mod.request_host_upscale(input_rel="a.mp4", output_rel="b.mp4")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_host_upscale_unreachable(monkeypatch):
    from server.services import realesrgan_upscale as mod

    async def _ensure():
        raise httpx.ConnectError("down")

    monkeypatch.setattr(mod, "ensure_proxy", _ensure)
    with pytest.raises(httpx.HTTPError):
        await mod.request_host_upscale(input_rel="a.mp4", output_rel="b.mp4")


@pytest.mark.unit
def test_projects_rel_path_round_trip(tmp_path):
    from server.services.realesrgan_upscale import projects_rel_path

    root = tmp_path / "projects"
    root.mkdir()
    inside = root / "proj" / "a.mp4"
    inside.parent.mkdir()
    inside.write_bytes(b"x")
    assert projects_rel_path(inside, root) == "proj/a.mp4"
    outside = tmp_path / "other.mp4"
    outside.write_bytes(b"y")
    with pytest.raises(ValueError):
        projects_rel_path(outside, root)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ensure_proxy_writes_flag_when_down(monkeypatch, tmp_path):
    from server.services import realesrgan_upscale as mod

    flag = tmp_path / "realesrgan_autostart.request"
    monkeypatch.setattr(mod, "_autostart_flag_path", lambda: flag)

    calls = {"n": 0}

    async def _health():
        calls["n"] += 1
        if calls["n"] < 3:
            return None
        return {"ok": True, "running": True, "port": 10005}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(mod, "proxy_health", _health)
    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client)

    async def _sleep(*_a, **_k):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _sleep)

    result = await mod.ensure_proxy(timeout_s=5)
    assert result["running"] is True
    assert calls["n"] >= 3
