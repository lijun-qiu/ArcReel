"""分镜 / 参考路线共用的本集预览合成与超分。

Mount prefix: /api/v1/projects/{project_name}
Paths: /episodes/{episode}/preview[|-compose|/upscale-preview]
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException

from lib.api_errors import ApiError, NotFoundError
from lib.i18n import Translator
from lib.narration_delivery import USE_TTS
from lib.project_manager import get_project_manager
from server.services.realesrgan_upscale import projects_rel_path, request_host_upscale

logger = logging.getLogger(__name__)

router = APIRouter()


def _episode_preview_payload(
    project_name: str,
    result: Any,
    *,
    exists: bool | None = None,
    upscaled: Any | None = None,
) -> dict[str, object]:
    revision: int | None = None
    abs_path = getattr(result, "absolute_path", None)
    if abs_path is not None:
        try:
            st = Path(abs_path).stat()
            revision = int(st.st_mtime_ns // 1_000_000)
        except OSError:
            revision = None
    body: dict[str, object] = {
        "relative_path": result.relative_path,
        "url": f"/api/v1/files/{quote(project_name, safe='')}/{result.relative_path}",
        "subtitles_webvtt": result.subtitles_webvtt,
        "subtitles": [cue.to_dict() for cue in result.subtitles],
        "revision": revision,
        "upscaled_exists": False,
        "upscaled_relative_path": None,
        "upscaled_url": None,
        "upscaled_revision": None,
    }
    if exists is not None:
        body["exists"] = exists
    if upscaled is not None:
        up_rev: int | None = None
        up_abs = getattr(upscaled, "absolute_path", None)
        if up_abs is not None:
            try:
                up_rev = int(Path(up_abs).stat().st_mtime_ns // 1_000_000)
            except OSError:
                up_rev = None
        body["upscaled_exists"] = True
        body["upscaled_relative_path"] = upscaled.relative_path
        body["upscaled_url"] = (
            f"/api/v1/files/{quote(project_name, safe='')}/{upscaled.relative_path}"
        )
        body["upscaled_revision"] = up_rev
    return body


def _load_episode(project_name: str, episode: int, _t: Translator) -> tuple[dict, dict, str]:
    """加载 project.json + 指定集剧本；不限制 generation_mode。"""
    try:
        project = get_project_manager().load_project(project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    episodes = project.get("episodes") or []
    meta = next((e for e in episodes if e.get("episode") == episode), None)
    if meta is None or not meta.get("script_file"):
        raise HTTPException(status_code=404, detail=_t("ref_episode_not_found", episode=episode))
    script_file = str(meta["script_file"])
    try:
        script = get_project_manager().load_script(project_name, script_file)
    except FileNotFoundError as exc:
        raise NotFoundError("script_not_found", name=script_file) from exc
    return project, script, script_file


async def _materialize_preview_presentations(project_name: str, episode: int):
    from server.services.presentation_read_model import (
        PresentationReadModelService,
        PresentationUnavailableError,
    )

    try:
        materialized = await PresentationReadModelService(get_project_manager()).materialize_episode(
            project_name=project_name,
            episode=episode,
            variant=USE_TTS,
        )
    except PresentationUnavailableError:
        logger.warning(
            "集预览成片物化失败，继续仅合成视频 project=%s episode=%s",
            project_name,
            episode,
            exc_info=True,
        )
        return None
    except Exception:
        logger.exception(
            "集预览成片物化异常，继续仅合成视频 project=%s episode=%s",
            project_name,
            episode,
        )
        return None
    return tuple(item.presentation for item in materialized.presentations)


@router.get("/projects/{project_name}/episodes/{episode}/preview")
async def get_episode_preview(project_name: str, episode: int, _t: Translator) -> dict[str, object]:
    """查询本集预览 MP4 是否已合成（不触发重新拼接）。"""
    _load_episode(project_name, episode, _t)

    from server.services.episode_preview_compose import (
        EpisodePreviewResult,
        resolve_episode_preview,
        resolve_episode_preview_upscaled,
        write_episode_preview_subtitles,
    )

    pm = get_project_manager()
    result = await asyncio.to_thread(
        resolve_episode_preview,
        project_name=project_name,
        episode=episode,
        project_manager=pm,
    )
    if result is None:
        return {
            "exists": False,
            "relative_path": None,
            "url": None,
            "revision": None,
            "subtitles_webvtt": None,
            "subtitles": [],
            "upscaled_exists": False,
            "upscaled_relative_path": None,
            "upscaled_url": None,
            "upscaled_revision": None,
        }

    if result.subtitles_webvtt is None:
        presentations = await _materialize_preview_presentations(project_name, episode)
        if presentations is not None:
            project_dir = Path(pm.get_project_path(project_name))
            written = await asyncio.to_thread(
                write_episode_preview_subtitles,
                project_dir=project_dir,
                episode=episode,
                presentations=presentations,
            )
            if written is not None:
                sub_rel, sub_abs, webvtt, cues = written
                result = EpisodePreviewResult(
                    relative_path=result.relative_path,
                    absolute_path=result.absolute_path,
                    subtitles_relative_path=sub_rel,
                    subtitles_absolute_path=sub_abs,
                    subtitles_webvtt=webvtt,
                    subtitles=cues,
                )

    upscaled = await asyncio.to_thread(
        resolve_episode_preview_upscaled,
        project_name=project_name,
        episode=episode,
        project_manager=pm,
    )
    return _episode_preview_payload(project_name, result, exists=True, upscaled=upscaled)


@router.post("/projects/{project_name}/episodes/{episode}/preview-compose")
async def compose_episode_preview_route(
    project_name: str, episode: int, _t: Translator
) -> dict[str, object]:
    """把本集全部镜头 / 单元硬切合成为 ``output/episode_N_preview.mp4``。"""
    _load_episode(project_name, episode, _t)

    from server.services.episode_preview_compose import (
        EmptyEpisodeUnitsError,
        FfmpegUnavailableError,
        MissingUnitClipsError,
        compose_episode_preview,
    )

    presentations = await _materialize_preview_presentations(project_name, episode)

    try:
        result = await asyncio.to_thread(
            compose_episode_preview,
            project_name=project_name,
            episode=episode,
            project_manager=get_project_manager(),
            presentations=presentations,
        )
    except MissingUnitClipsError as exc:
        raise ApiError(
            "episode_preview_missing_clips",
            status_code=422,
            episode=episode,
            unit_ids=", ".join(exc.missing_unit_ids),
        ) from exc
    except EmptyEpisodeUnitsError as exc:
        raise ApiError("episode_preview_empty", status_code=422, episode=episode) from exc
    except FfmpegUnavailableError as exc:
        raise ApiError("episode_preview_ffmpeg_unavailable", status_code=503) from exc
    except FileNotFoundError:
        raise
    except Exception as exc:
        logger.exception(
            "合成本集预览失败 project=%s episode=%s",
            project_name,
            episode,
        )
        raise ApiError("episode_preview_failed", status_code=500) from exc

    from server.services.episode_preview_compose import resolve_episode_preview_upscaled

    upscaled = await asyncio.to_thread(
        resolve_episode_preview_upscaled,
        project_name=project_name,
        episode=episode,
        project_manager=get_project_manager(),
    )
    return _episode_preview_payload(project_name, result, upscaled=upscaled)


@router.post("/projects/{project_name}/episodes/{episode}/upscale-preview")
async def upscale_episode_preview(project_name: str, episode: int, _t: Translator) -> dict[str, object]:
    """对本集已合成预览做 Real-ESRGAN 超分，写入独立的 ``*_preview_upscaled.mp4``。"""
    _load_episode(project_name, episode, _t)

    from server.services.episode_preview_compose import (
        preview_upscaled_relative_path,
        resolve_episode_preview,
        resolve_episode_preview_upscaled,
    )

    pm = get_project_manager()
    project_path = pm.get_project_path(project_name)
    projects_root = project_path.parent

    existing = await asyncio.to_thread(
        resolve_episode_preview,
        project_name=project_name,
        episode=episode,
        project_manager=pm,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail=_t("episode_preview_not_found", episode=episode))

    relative = preview_upscaled_relative_path(episode)
    try:
        target = (project_path / relative).resolve()
        if not str(target).startswith(str(project_path.resolve()) + os.sep):
            raise ValueError("path escapes project")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_t("invalid_resource_id", resource_id=relative)) from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f".{target.stem}.{uuid.uuid4().hex[:8]}.tmp{target.suffix}")

    try:
        input_rel = projects_rel_path(existing.absolute_path, projects_root)
        output_rel = projects_rel_path(staged, projects_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_t("invalid_resource_id", resource_id=relative)) from exc

    try:
        await request_host_upscale(input_rel=input_rel, output_rel=output_rel)
    except httpx.HTTPError as exc:
        await asyncio.to_thread(staged.unlink, missing_ok=True)
        raise HTTPException(status_code=503, detail=_t("realesrgan_proxy_unreachable")) from exc
    except RuntimeError as exc:
        await asyncio.to_thread(staged.unlink, missing_ok=True)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not staged.is_file():
        raise HTTPException(status_code=502, detail=_t("realesrgan_output_missing"))

    try:
        await asyncio.to_thread(os.replace, staged, target)
    except OSError:
        await asyncio.to_thread(staged.unlink, missing_ok=True)
        raise

    refreshed = await asyncio.to_thread(
        resolve_episode_preview,
        project_name=project_name,
        episode=episode,
        project_manager=pm,
    )
    upscaled = await asyncio.to_thread(
        resolve_episode_preview_upscaled,
        project_name=project_name,
        episode=episode,
        project_manager=pm,
    )
    if refreshed is None or upscaled is None:
        raise HTTPException(status_code=502, detail=_t("realesrgan_output_missing"))
    return _episode_preview_payload(project_name, refreshed, exists=True, upscaled=upscaled)
