"""把本集镜头 / 单元的当前成片硬切合成为集预览 MP4。

覆盖参考生视频（``video_units``）与分镜生视频（``segments`` / ``scenes`` / ``shots``）。
"""

from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.path_safety import PathTraversalError, safe_join
from lib.project_manager import ProjectManager, is_reference_video_project
from lib.script_models import get_generated_assets
from lib.script_skeleton import resolve_kind_items
from lib.speech_composition import SpeechOwner
from lib.speech_presentation import PresentationValue, SubtitleCue, subtitles_webvtt

logger = logging.getLogger(__name__)

ConcatenateFn = Callable[[list[Path], Path], None]
RenderMixFn = Callable[["PresentationMixSpec", Path], None]


class NotReferenceVideoScriptError(ValueError):
    """剧本不是参考生视频骨架（无 ``video_units``）。"""


class EmptyEpisodeUnitsError(ValueError):
    """本集没有任何单元。"""


class MissingUnitClipsError(ValueError):
    """部分单元缺少可拼接的成片文件。"""

    def __init__(self, missing_unit_ids: list[str]) -> None:
        self.missing_unit_ids = list(missing_unit_ids)
        missing = ", ".join(self.missing_unit_ids)
        super().__init__(f"missing unit clips: {missing}")


class FfmpegUnavailableError(RuntimeError):
    """PATH 中找不到可用的 ffmpeg / ffprobe。"""


@dataclass(frozen=True, slots=True)
class EpisodePreviewResult:
    relative_path: str
    absolute_path: Path
    subtitles_relative_path: str | None = None
    subtitles_absolute_path: Path | None = None
    subtitles_webvtt: str | None = None
    subtitles: tuple[SubtitleCue, ...] = ()


@dataclass(frozen=True, slots=True)
class PresentationMixSpec:
    """一单元成片预览混音：视频原声增益 + 可选旁白 TTS。"""

    video_path: Path
    video_gain: float
    narration_path: Path | None = None
    narration_gain: float | None = None
    narration_start_microseconds: int = 0

    def needs_render(self) -> bool:
        if self.narration_path is not None:
            return True
        return self.video_gain != 1.0


def preview_relative_path(episode: int) -> str:
    return f"output/episode_{episode}_preview.mp4"


def preview_upscaled_relative_path(episode: int) -> str:
    """Separate Real-ESRGAN output; keeps the composed preview untouched."""
    return f"output/episode_{episode}_preview_upscaled.mp4"


def preview_subtitles_relative_path(episode: int) -> str:
    return f"output/episode_{episode}_preview.vtt"


def preview_subtitles_json_relative_path(episode: int) -> str:
    return f"output/episode_{episode}_preview.subtitles.json"


def offset_presentation_subtitle_cues(
    presentations: Sequence[PresentationValue] | Iterable[PresentationValue],
) -> tuple[SubtitleCue, ...]:
    """把各单元机械字幕按视频轨实际时长累加到集时间轴（与剪映导出同口径）。"""
    offset_microseconds = 0
    cues: list[SubtitleCue] = []
    for presentation in presentations:
        for cue in presentation.subtitles:
            cues.append(
                SubtitleCue(
                    start_microseconds=offset_microseconds + cue.start_microseconds,
                    duration_microseconds=cue.duration_microseconds,
                    text=cue.text,
                    owner=cue.owner,
                    speaker=cue.speaker,
                )
            )
        offset_microseconds += presentation.video.duration_microseconds
    return tuple(cues)


def _unlink_preview_subtitle_sidecars(project_dir: Path, episode: int) -> None:
    for relative in (
        preview_subtitles_relative_path(episode),
        preview_subtitles_json_relative_path(episode),
    ):
        try:
            path = safe_join(project_dir, relative, require_file=True)
        except (PathTraversalError, FileNotFoundError, TypeError):
            continue
        path.unlink(missing_ok=True)


def write_episode_preview_subtitles(
    *,
    project_dir: Path,
    episode: int,
    presentations: Sequence[PresentationValue] | Iterable[PresentationValue],
) -> tuple[str, Path, str, tuple[SubtitleCue, ...]] | None:
    """写出集预览机械字幕 sidecar；无可写字幕时返回 ``None``（不留空文件）。"""
    cues = offset_presentation_subtitle_cues(presentations)
    if not cues:
        _unlink_preview_subtitle_sidecars(project_dir, episode)
        return None

    webvtt = subtitles_webvtt(cues)
    relative = preview_subtitles_relative_path(episode)
    absolute = safe_join(project_dir, relative, allow_base=False)
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text(webvtt, encoding="utf-8")
    json_path = safe_join(project_dir, preview_subtitles_json_relative_path(episode), allow_base=False)
    json_path.write_text(
        json.dumps([cue.to_dict() for cue in cues], ensure_ascii=False),
        encoding="utf-8",
    )
    return relative, absolute, webvtt, cues


def _cues_from_dicts(raw: object) -> tuple[SubtitleCue, ...]:
    if not isinstance(raw, list):
        return ()
    cues: list[SubtitleCue] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        start = item.get("start_microseconds")
        duration = item.get("duration_microseconds")
        text = item.get("text")
        owner_raw = item.get("owner")
        if type(start) is not int or type(duration) is not int or not isinstance(text, str):
            continue
        try:
            owner = SpeechOwner(owner_raw) if isinstance(owner_raw, str) else SpeechOwner.NARRATOR
        except ValueError:
            owner = SpeechOwner.NARRATOR
        speaker = item.get("speaker")
        cues.append(
            SubtitleCue(
                start_microseconds=start,
                duration_microseconds=duration,
                text=text,
                owner=owner,
                speaker=speaker if isinstance(speaker, str) else None,
            )
        )
    return tuple(cues)


def _load_preview_subtitles(
    project_dir: Path,
    episode: int,
) -> tuple[str, Path, str, tuple[SubtitleCue, ...]] | None:
    relative = preview_subtitles_relative_path(episode)
    try:
        absolute = safe_join(project_dir, relative, require_file=True)
    except (PathTraversalError, FileNotFoundError, TypeError):
        return None
    text = absolute.read_text(encoding="utf-8")
    if not text.strip():
        return None
    cues: tuple[SubtitleCue, ...] = ()
    try:
        json_path = safe_join(
            project_dir,
            preview_subtitles_json_relative_path(episode),
            require_file=True,
        )
        cues = _cues_from_dicts(json.loads(json_path.read_text(encoding="utf-8")))
    except (PathTraversalError, FileNotFoundError, TypeError, json.JSONDecodeError):
        cues = ()
    return relative, absolute, text, cues


def resolve_episode_preview(
    *,
    project_name: str,
    episode: int,
    project_manager: ProjectManager,
) -> EpisodePreviewResult | None:
    """若 ``output/episode_N_preview.mp4`` 已存在则返回路径，否则 ``None``。"""
    project_dir = Path(project_manager.get_project_path(project_name))
    relative = preview_relative_path(episode)
    try:
        absolute = safe_join(project_dir, relative, require_file=True)
    except (PathTraversalError, FileNotFoundError, TypeError):
        return None
    subtitles = _load_preview_subtitles(project_dir, episode)
    if subtitles is None:
        return EpisodePreviewResult(relative_path=relative, absolute_path=absolute)
    sub_rel, sub_abs, webvtt, cues = subtitles
    return EpisodePreviewResult(
        relative_path=relative,
        absolute_path=absolute,
        subtitles_relative_path=sub_rel,
        subtitles_absolute_path=sub_abs,
        subtitles_webvtt=webvtt,
        subtitles=cues,
    )


def resolve_episode_preview_upscaled(
    *,
    project_name: str,
    episode: int,
    project_manager: ProjectManager,
) -> EpisodePreviewResult | None:
    """若 ``output/episode_N_preview_upscaled.mp4`` 已存在则返回（复用合成预览字幕）。"""
    project_dir = Path(project_manager.get_project_path(project_name))
    relative = preview_upscaled_relative_path(episode)
    try:
        absolute = safe_join(project_dir, relative, require_file=True)
    except (PathTraversalError, FileNotFoundError, TypeError):
        return None
    base = resolve_episode_preview(
        project_name=project_name,
        episode=episode,
        project_manager=project_manager,
    )
    if base is None:
        return EpisodePreviewResult(relative_path=relative, absolute_path=absolute)
    return EpisodePreviewResult(
        relative_path=relative,
        absolute_path=absolute,
        subtitles_relative_path=base.subtitles_relative_path,
        subtitles_absolute_path=base.subtitles_absolute_path,
        subtitles_webvtt=base.subtitles_webvtt,
        subtitles=base.subtitles,
    )


def collect_script_item_clips(
    project_dir: Path,
    script: dict[str, Any],
    *,
    kind: str | None = None,
) -> list[Path]:
    """按剧本顺序收集本集条目成片（任意骨架族）；缺片则抛出 ``MissingUnitClipsError``。"""
    raw_items, id_field, resolved_kind = resolve_kind_items(script, kind=kind)
    if not isinstance(raw_items, list):
        raise EmptyEpisodeUnitsError(f"episode has no {resolved_kind} list")
    if not raw_items:
        raise EmptyEpisodeUnitsError(f"episode has no {resolved_kind}")

    clips: list[Path] = []
    missing: list[str] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get(id_field) or "?")
        clip_rel = get_generated_assets(raw).get("video_clip")
        if not isinstance(clip_rel, str) or not clip_rel.strip():
            missing.append(item_id)
            continue
        try:
            clip_path = safe_join(project_dir, clip_rel, require_file=True)
        except (PathTraversalError, FileNotFoundError, TypeError):
            missing.append(item_id)
            continue
        clips.append(clip_path)

    if missing:
        raise MissingUnitClipsError(missing)
    if not clips:
        raise EmptyEpisodeUnitsError(f"episode has no usable {resolved_kind}")
    return clips


def collect_reference_unit_clips(project_dir: Path, script: dict[str, Any]) -> list[Path]:
    """按剧本顺序收集本集参考视频单元成片；缺片则抛出 ``MissingUnitClipsError``。"""
    units = script.get("video_units")
    if not isinstance(units, list):
        raise NotReferenceVideoScriptError("script has no video_units[]")
    return collect_script_item_clips(project_dir, script, kind="video_units")


def _presentation_media_path(project_dir: Path, track: object, *, field: str) -> Path:
    media = getattr(track, "media", None)
    artifact = getattr(media, "artifact_path", None) if media is not None else None
    if not isinstance(artifact, str) or not artifact.strip():
        raise ValueError(f"presentation {field} lacks artifact_path")
    return safe_join(project_dir, artifact, require_file=True)


def presentations_have_mixable_media(
    presentations: Sequence[PresentationValue] | Iterable[PresentationValue],
) -> bool:
    """字幕 stub 只有 duration 时返回 False，完整 presentation 才走混音烘焙。"""
    items = list(presentations)
    if not items:
        return False
    for presentation in items:
        video = getattr(presentation, "video", None)
        media = getattr(video, "media", None) if video is not None else None
        artifact = getattr(media, "artifact_path", None) if media is not None else None
        if not isinstance(artifact, str) or not artifact.strip():
            return False
    return True


def presentation_mix_specs(
    project_dir: Path,
    presentations: Sequence[PresentationValue] | Iterable[PresentationValue],
) -> list[PresentationMixSpec]:
    """从 presentation 读取模型抽出每单元混音规格（路径已解析到项目目录）。"""
    specs: list[PresentationMixSpec] = []
    for presentation in presentations:
        video = presentation.video
        video_path = _presentation_media_path(project_dir, video, field="video")
        audio_enabled = bool(getattr(video, "audio_enabled", True))
        raw_gain = getattr(video, "gain", 1.0)
        if isinstance(raw_gain, bool) or not isinstance(raw_gain, (int, float)):
            raise TypeError("presentation video.gain must be a number")
        video_gain = float(raw_gain) if audio_enabled else 0.0

        narration = getattr(presentation, "narration_audio", None)
        if narration is None:
            specs.append(PresentationMixSpec(video_path=video_path, video_gain=video_gain))
            continue

        narration_path = _presentation_media_path(project_dir, narration, field="narration_audio")
        narr_gain = getattr(narration, "gain", 1.0)
        if isinstance(narr_gain, bool) or not isinstance(narr_gain, (int, float)):
            raise TypeError("presentation narration_audio.gain must be a number")
        start = getattr(narration, "start_microseconds", 0)
        if type(start) is not int or start < 0:
            raise ValueError("narration start_microseconds must be a non-negative int")
        specs.append(
            PresentationMixSpec(
                video_path=video_path,
                video_gain=video_gain,
                narration_path=narration_path,
                narration_gain=float(narr_gain),
                narration_start_microseconds=start,
            )
        )
    return specs


def materialize_presentation_mix_clips(
    specs: Sequence[PresentationMixSpec],
    *,
    work_dir: Path,
    render: RenderMixFn,
) -> list[Path]:
    """满增益且无 TTS 的单元直通原片；其余烘焙到 ``work_dir``。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    for index, spec in enumerate(specs):
        if not spec.needs_render():
            clips.append(spec.video_path.resolve())
            continue
        output = work_dir / f"mix_{index:04d}.mp4"
        render(spec, output)
        clips.append(output.resolve())
    return clips


def render_presentation_mix_clip(spec: PresentationMixSpec, output: Path) -> None:
    """用 ffmpeg 按 presentation 增益把视频原声与可选 TTS 烘焙成单文件。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    if spec.narration_path is None:
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(spec.video_path),
            "-filter:a",
            f"volume={spec.video_gain}",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            str(output),
        ]
    else:
        if spec.narration_gain is None:
            raise ValueError("narration_gain is required when narration_path is set")
        delay_ms = spec.narration_start_microseconds // 1000
        narr_filter = f"volume={spec.narration_gain}"
        if delay_ms > 0:
            narr_filter = f"adelay={delay_ms}|{delay_ms},{narr_filter}"
        filter_complex = (
            f"[0:a]volume={spec.video_gain}[va];"
            f"[1:a]{narr_filter}[na];"
            "[va][na]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(spec.video_path),
            "-i",
            str(spec.narration_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            str(output),
        ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        raise RuntimeError(f"ffmpeg presentation mix failed: {detail}")


def _load_compose_video_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = (
        repo_root / "agent_runtime_profile" / ".claude" / "skills" / "compose-video" / "scripts" / "compose_video.py"
    )
    spec = importlib.util.spec_from_file_location("_compose_video_for_preview", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load compose_video helpers from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not module.check_ffmpeg():
        raise FfmpegUnavailableError(module.FFMPEG_TOOLS_HINT)
    return module


def _load_concatenate_simple() -> ConcatenateFn:
    """复用 compose-video skill 的 cut 拼接实现，避免复制 ffmpeg 滤镜逻辑。"""
    return _load_compose_video_module().concatenate_simple


def compose_episode_preview(
    *,
    project_name: str,
    episode: int,
    project_manager: ProjectManager,
    concatenate: ConcatenateFn | None = None,
    render_mix: RenderMixFn | None = None,
    presentations: Sequence[PresentationValue] | Iterable[PresentationValue] | None = None,
) -> EpisodePreviewResult:
    """读取本集剧本，按 presentation 混音后硬切拼接为集预览 MP4。

    适用于参考生视频与分镜生视频。``presentations`` 若给出且带可解析媒体路径：按
    各条目视频原声增益与可选 TTS 烘焙后再拼接；否则回退为剧本 ``video_clip`` 直拼。
    """
    project = project_manager.load_project(project_name)

    episodes = project.get("episodes") or []
    meta = next((e for e in episodes if e.get("episode") == episode), None)
    if meta is None or not meta.get("script_file"):
        raise FileNotFoundError(f"episode {episode} not found")
    script_file = str(meta["script_file"])
    script = project_manager.load_script(project_name, script_file)

    project_dir = Path(project_manager.get_project_path(project_name))
    presentation_list = list(presentations) if presentations is not None else None

    relative = preview_relative_path(episode)
    final_path = safe_join(project_dir, relative, allow_base=False)
    final_path.parent.mkdir(parents=True, exist_ok=True)

    concat = concatenate or _load_concatenate_simple()
    mix_render = render_mix or render_presentation_mix_clip

    # 先写临时文件再替换，避免合成中途失败留下半截预览。
    with tempfile.NamedTemporaryFile(
        prefix=f"episode_{episode}_preview_",
        suffix=".mp4",
        dir=final_path.parent,
        delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with tempfile.TemporaryDirectory(prefix=f"episode_{episode}_mix_", dir=final_path.parent) as mix_dir:
            if presentation_list is not None and presentations_have_mixable_media(presentation_list):
                specs = presentation_mix_specs(project_dir, presentation_list)
                clips = materialize_presentation_mix_clips(
                    specs,
                    work_dir=Path(mix_dir),
                    render=mix_render,
                )
            else:
                clips = collect_script_item_clips(project_dir, script)
            concat(clips, tmp_path)
        tmp_path.replace(final_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    subtitle_fields: dict[str, Any] = {}
    if presentation_list is not None:
        written = write_episode_preview_subtitles(
            project_dir=project_dir,
            episode=episode,
            presentations=presentation_list,
        )
        if written is not None:
            sub_rel, sub_abs, webvtt, cues = written
            subtitle_fields = {
                "subtitles_relative_path": sub_rel,
                "subtitles_absolute_path": sub_abs,
                "subtitles_webvtt": webvtt,
                "subtitles": cues,
            }

    logger.info(
        "composed episode preview project=%s episode=%s path=%s clips=%d subtitles=%s",
        project_name,
        episode,
        relative,
        len(clips),
        "yes" if subtitle_fields.get("subtitles_webvtt") else "no",
    )
    return EpisodePreviewResult(
        relative_path=relative,
        absolute_path=final_path,
        **subtitle_fields,
    )


def compose_reference_episode_preview(
    *,
    project_name: str,
    episode: int,
    project_manager: ProjectManager,
    concatenate: ConcatenateFn | None = None,
    render_mix: RenderMixFn | None = None,
    presentations: Sequence[PresentationValue] | Iterable[PresentationValue] | None = None,
) -> EpisodePreviewResult:
    """参考生视频集预览：校验 generation_mode 后委托 ``compose_episode_preview``。"""
    project = project_manager.load_project(project_name)
    if not is_reference_video_project(project):
        raise NotReferenceVideoScriptError("project is not reference_video generation_mode")
    return compose_episode_preview(
        project_name=project_name,
        episode=episode,
        project_manager=project_manager,
        concatenate=concatenate,
        render_mix=render_mix,
        presentations=presentations,
    )
