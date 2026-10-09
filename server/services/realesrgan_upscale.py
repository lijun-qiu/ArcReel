"""Call the Windows Real-ESRGAN host proxy from ArcReel (Docker)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx

_DEFAULT_PROXY = "http://host.docker.internal:10005"
# Upscale can take many minutes for long clips.
_TIMEOUT = httpx.Timeout(3600.0, connect=5.0)
_HEALTH_TIMEOUT = httpx.Timeout(3.0, connect=2.0)
# Host-side fallback when ArcReel is not running in Docker (dev).
_HOST_AUTOSTART_FLAG = Path(__file__).resolve().parents[2] / "deploy" / "logs" / "realesrgan_autostart.request"


def realesrgan_proxy_base() -> str:
    return (os.environ.get("REALESRGAN_PROXY_BASE_URL") or _DEFAULT_PROXY).rstrip("/")


def projects_rel_path(abs_path: Path, projects_root: Path) -> str:
    """Return posix-relative path under the projects root for the host proxy."""
    root = projects_root.resolve()
    resolved = abs_path.resolve()
    rel = resolved.relative_to(root)
    return rel.as_posix()


def _autostart_flag_path() -> Path:
    env = os.environ.get("REALESRGAN_AUTOSTART_FLAG")
    if env:
        return Path(env)
    if Path("/app/logs").is_dir():
        return Path("/app/logs/realesrgan_autostart.request")
    return _HOST_AUTOSTART_FLAG


def _request_autostart() -> None:
    flag = _autostart_flag_path()
    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("ensure\n", encoding="utf-8")
    except OSError:
        pass


async def proxy_health() -> dict[str, Any] | None:
    url = f"{realesrgan_proxy_base()}/health"
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
            resp = await client.get(url)
        payload = resp.json()
        if isinstance(payload, dict) and resp.status_code < 500:
            return payload
    except (httpx.HTTPError, ValueError):
        return None
    return None


async def ensure_proxy(timeout_s: float = 25.0) -> dict[str, Any]:
    """Ensure the host proxy is reachable; write autostart flag and poll if down."""
    health = await proxy_health()
    if health and health.get("running"):
        return {"ok": True, "running": True, "started": False, **health}

    _request_autostart()
    # Also try POST /ensure in case an old proxy / supervisor is already listening.
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
            await client.post(f"{realesrgan_proxy_base()}/ensure")
    except httpx.HTTPError:
        pass

    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.75)
        health = await proxy_health()
        if health and health.get("running"):
            return {"ok": True, "running": True, "started": True, **health}
        _request_autostart()

    raise httpx.ConnectError(f"RealESRGAN proxy unreachable at {realesrgan_proxy_base()}")


async def fetch_progress() -> dict[str, Any]:
    """GET /progress from the host proxy (empty/idle if unreachable)."""
    url = f"{realesrgan_proxy_base()}/progress"
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
            resp = await client.get(url)
        payload = resp.json()
        if isinstance(payload, dict):
            return payload
    except (httpx.HTTPError, ValueError):
        pass
    return {
        "ok": False,
        "busy": False,
        "phase": "idle",
        "percent": 0,
        "message": "",
        "error": "unreachable",
    }


async def request_host_upscale(
    *,
    input_rel: str,
    output_rel: str,
    scale: int = 2,
    model: str = "realesr-animevideov3",
) -> dict[str, Any]:
    """POST /upscale on the host proxy. Raises httpx.HTTPError or ValueError."""
    await ensure_proxy()
    url = f"{realesrgan_proxy_base()}/upscale"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            url,
            json={
                "input_rel": input_rel,
                "output_rel": output_rel,
                "scale": scale,
                "model": model,
            },
        )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise ValueError(f"RealESRGAN proxy returned non-JSON ({resp.status_code})") from exc
    if not isinstance(payload, dict):
        raise ValueError("RealESRGAN proxy returned invalid payload")
    if resp.status_code >= 400 or not payload.get("ok"):
        detail = str(payload.get("error") or payload.get("detail") or f"HTTP {resp.status_code}")
        raise RuntimeError(detail)
    return payload
