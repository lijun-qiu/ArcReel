"""Local Real-ESRGAN host proxy — progress / ensure for UI polling."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from server.services.realesrgan_upscale import ensure_proxy, fetch_progress, proxy_health

router = APIRouter(prefix="/local-services/realesrgan", tags=["Local RealESRGAN"])


@router.get("/progress")
async def realesrgan_progress() -> dict[str, Any]:
    """Live upscale progress from the Windows host proxy."""
    return await fetch_progress()


@router.get("/health")
async def realesrgan_health() -> dict[str, Any]:
    health = await proxy_health()
    if health is None:
        return {"ok": False, "running": False, "error": "unreachable"}
    return health


@router.post("/ensure")
async def realesrgan_ensure() -> dict[str, Any]:
    """Ask the host supervisor to start the proxy if it is down."""
    try:
        return await ensure_proxy()
    except Exception as exc:  # noqa: BLE001 — surface connect failures to UI
        return {"ok": False, "running": False, "error": str(exc)}
