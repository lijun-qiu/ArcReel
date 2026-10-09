"""Local IndexTTS2 control — proxies to the host control agent on :10004.

The speech proxy (GPU) listens on host :10003; ArcReel in Docker cannot spawn it
directly, so UI start/unload buttons hit this router which forwards to
``host.docker.internal:10004``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/local-services/indextts", tags=["Local IndexTTS"])

_DEFAULT_CONTROL = "http://host.docker.internal:10004"
_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


def _control_base() -> str:
    return (os.environ.get("INDEXTTS_CONTROL_BASE_URL") or _DEFAULT_CONTROL).rstrip("/")


class IndexTtsStatus(BaseModel):
    ok: bool = False
    running: bool = False
    available: bool | None = None
    started: bool | None = None
    already_running: bool | None = None
    killed: int | None = None
    error: str | None = None
    detail: str | None = None
    root: str | None = None
    model_dir: str | None = None
    missing_models: list[str] = Field(default_factory=list)
    control_reachable: bool = False
    control_base: str = ""
    speech_port: int | None = None
    control_port: int | None = None


async def _forward(method: str, path: str) -> dict[str, Any]:
    url = f"{_control_base()}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(method, url)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "IndexTTS host control unreachable "
                f"({_control_base()}). Run deploy/start_indextts_control.ps1 on the host."
            ),
        ) from exc

    try:
        payload = resp.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"IndexTTS control returned non-JSON ({resp.status_code})",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="IndexTTS control returned invalid payload")
    if resp.status_code >= 400 and not payload.get("ok") and not payload.get("running"):
        raise HTTPException(
            status_code=resp.status_code if resp.status_code < 500 else 503,
            detail=str(payload.get("error") or payload.get("detail") or "IndexTTS control failed"),
        )
    payload["control_reachable"] = True
    payload["control_base"] = _control_base()
    return payload


def _to_status(payload: dict[str, Any]) -> IndexTtsStatus:
    return IndexTtsStatus(
        ok=bool(payload.get("ok")),
        running=bool(payload.get("running")),
        available=payload.get("available") if "available" in payload else bool(payload.get("running")),
        started=payload.get("started") if "started" in payload else None,
        already_running=payload.get("already_running") if "already_running" in payload else None,
        killed=payload.get("killed") if isinstance(payload.get("killed"), int) else None,
        error=str(payload["error"]) if payload.get("error") else None,
        detail=str(payload["detail"]) if payload.get("detail") else None,
        root=str(payload["root"]) if payload.get("root") else None,
        model_dir=str(payload["model_dir"]) if payload.get("model_dir") else None,
        missing_models=[str(x) for x in (payload.get("missing_models") or [])],
        control_reachable=bool(payload.get("control_reachable")),
        control_base=str(payload.get("control_base") or _control_base()),
        speech_port=payload.get("speech_port") if isinstance(payload.get("speech_port"), int) else None,
        control_port=payload.get("control_port") if isinstance(payload.get("control_port"), int) else None,
    )


@router.get("/status", response_model=IndexTtsStatus)
async def indextts_status() -> IndexTtsStatus:
    try:
        payload = await _forward("GET", "/status")
        return _to_status(payload)
    except HTTPException as exc:
        if exc.status_code == 503:
            return IndexTtsStatus(
                ok=False,
                running=False,
                available=False,
                control_reachable=False,
                control_base=_control_base(),
                error=str(exc.detail),
            )
        raise


@router.post("/start", response_model=IndexTtsStatus)
async def indextts_start() -> IndexTtsStatus:
    payload = await _forward("POST", "/start")
    return _to_status(payload)


@router.post("/unload", response_model=IndexTtsStatus)
async def indextts_unload() -> IndexTtsStatus:
    payload = await _forward("POST", "/unload")
    return _to_status(payload)
