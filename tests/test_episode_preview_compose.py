"""集预览视频：按参考视频单元顺序收集成片路径并写出 preview MP4。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.speech_composition import SpeechOwner
from lib.speech_presentation import SubtitleCue
from server.services.episode_preview_compose import (
    EmptyEpisodeUnitsError,
    MissingUnitClipsError,
    NotReferenceVideoScriptError,
    PresentationMixSpec,
    collect_reference_unit_clips,
    collect_script_item_clips,
    compose_episode_preview,
    compose_reference_episode_preview,
    materialize_presentation_mix_clips,
    offset_presentation_subtitle_cues,
    presentation_mix_specs,
    preview_relative_path,
    resolve_episode_preview,
    write_episode_preview_subtitles,
)

pytestmark = pytest.mark.unit


def _unit(unit_id: str, clip: str | None = None) -> dict:
    assets = {"video_clip": clip} if clip is not None else {}
    return {"unit_id": unit_id, "duration_seconds": 5, "generated_assets": assets}


def _presentation(*, duration_us: int, cues: list[SubtitleCue]) -> SimpleNamespace:
    return SimpleNamespace(
        video=SimpleNamespace(duration_microseconds=duration_us),
        subtitles=tuple(cues),
    )


def test_preview_relative_path() -> None:
    assert preview_relative_path(3) == "output/episode_3_preview.mp4"


def test_offset_presentation_subtitle_cues_accumulates_by_video_duration() -> None:
    first = _presentation(
        duration_us=4_000_000,
        cues=[
            SubtitleCue(0, 2_000_000, "甲", SpeechOwner.CHARACTER, "A"),
            SubtitleCue(2_000_000, 2_000_000, "乙", SpeechOwner.NARRATOR, None),
        ],
    )
    second = _presentation(
        duration_us=3_000_000,
        cues=[SubtitleCue(500_000, 1_000_000, "丙", SpeechOwner.CHARACTER, "B")],
    )

    cues = offset_presentation_subtitle_cues([first, second])

    assert [(c.start_microseconds, c.duration_microseconds, c.text) for c in cues] == [
        (0, 2_000_000, "甲"),
        (2_000_000, 2_000_000, "乙"),
        (4_500_000, 1_000_000, "丙"),
    ]


def test_write_episode_preview_subtitles_writes_vtt_and_json(tmp_path: Path) -> None:
    presentations = [
        _presentation(
            duration_us=2_000_000,
            cues=[SubtitleCue(0, 2_000_000, "你好", SpeechOwner.NARRATOR, None)],
        )
    ]

    written = write_episode_preview_subtitles(
        project_dir=tmp_path,
        episode=2,
        presentations=presentations,
    )

    assert written is not None
    relative, absolute, webvtt, cues = written
    assert relative == "output/episode_2_preview.vtt"
    assert absolute.is_file()
    assert "你好" in webvtt
    assert cues[0].text == "你好"
    assert (tmp_path / "output" / "episode_2_preview.subtitles.json").is_file()


def test_collect_clips_in_script_order(tmp_path: Path) -> None:
    (tmp_path / "reference_videos").mkdir()
    a = tmp_path / "reference_videos" / "E1U01.mp4"
    b = tmp_path / "reference_videos" / "E1U02.mp4"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    script = {
        "content_mode": "drama",
        "generation_mode": "reference_video",
        "video_units": [_unit("E1U01", "reference_videos/E1U01.mp4"), _unit("E1U02", "reference_videos/E1U02.mp4")],
    }

    clips = collect_reference_unit_clips(tmp_path, script)

    assert clips == [a.resolve(), b.resolve()]


def test_collect_rejects_non_reference_script(tmp_path: Path) -> None:
    script = {"scenes": [{"scene_id": "E1S01"}]}
    with pytest.raises(NotReferenceVideoScriptError):
        collect_reference_unit_clips(tmp_path, script)


def test_collect_storyboard_scene_clips_in_script_order(tmp_path: Path) -> None:
    (tmp_path / "videos").mkdir()
    a = tmp_path / "videos" / "E1S01.mp4"
    b = tmp_path / "videos" / "E1S02.mp4"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    script = {
        "content_mode": "drama",
        "scenes": [
            {"scene_id": "E1S01", "generated_assets": {"video_clip": "videos/E1S01.mp4"}},
            {"scene_id": "E1S02", "generated_assets": {"video_clip": "videos/E1S02.mp4"}},
        ],
    }

    clips = collect_script_item_clips(tmp_path, script)

    assert clips == [a.resolve(), b.resolve()]


def test_collect_storyboard_lists_missing_clips(tmp_path: Path) -> None:
    script = {
        "content_mode": "drama",
        "scenes": [
            {"scene_id": "E1S01", "generated_assets": {"video_clip": "videos/missing.mp4"}},
            {"scene_id": "E1S02"},
        ],
    }

    with pytest.raises(MissingUnitClipsError) as exc_info:
        collect_script_item_clips(tmp_path, script)

    assert exc_info.value.missing_unit_ids == ["E1S01", "E1S02"]


def test_collect_rejects_empty_units(tmp_path: Path) -> None:
    script = {"video_units": []}
    with pytest.raises(EmptyEpisodeUnitsError):
        collect_reference_unit_clips(tmp_path, script)


def test_collect_lists_missing_clips(tmp_path: Path) -> None:
    (tmp_path / "reference_videos").mkdir()
    present = tmp_path / "reference_videos" / "E1U01.mp4"
    present.write_bytes(b"a")
    script = {
        "video_units": [
            _unit("E1U01", "reference_videos/E1U01.mp4"),
            _unit("E1U02"),
            _unit("E1U03", "reference_videos/missing.mp4"),
        ],
    }

    with pytest.raises(MissingUnitClipsError) as exc_info:
        collect_reference_unit_clips(tmp_path, script)

    assert exc_info.value.missing_unit_ids == ["E1U02", "E1U03"]


def test_compose_writes_preview_via_injected_concat(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "reference_videos").mkdir()
    clip = project_dir / "reference_videos" / "E1U01.mp4"
    clip.write_bytes(b"clip")
    (project_dir / "project.json").write_text(
        '{"name":"proj","generation_mode":"reference_video","episodes":[{"episode":1,"script_file":"scripts/episode_1.json"}]}',
        encoding="utf-8",
    )
    scripts = project_dir / "scripts"
    scripts.mkdir()
    (scripts / "episode_1.json").write_text(
        '{"video_units":[{"unit_id":"E1U01","duration_seconds":5,"generated_assets":{"video_clip":"reference_videos/E1U01.mp4"}}]}',
        encoding="utf-8",
    )

    written: list[tuple[list[Path], Path]] = []

    def fake_concat(paths: list[Path], output: Path) -> None:
        written.append((paths, output))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"preview")

    from lib.project_manager import ProjectManager

    pm = ProjectManager(tmp_path)
    presentations = [
        _presentation(
            duration_us=5_000_000,
            cues=[SubtitleCue(0, 5_000_000, "机械字幕", SpeechOwner.NARRATOR, None)],
        )
    ]
    result = compose_reference_episode_preview(
        project_name="proj",
        episode=1,
        project_manager=pm,
        concatenate=fake_concat,
        presentations=presentations,
    )

    assert result.relative_path == "output/episode_1_preview.mp4"
    assert result.absolute_path.is_file()
    assert result.absolute_path.read_bytes() == b"preview"
    assert result.subtitles_webvtt is not None
    assert "机械字幕" in result.subtitles_webvtt
    assert written[0][0] == [clip.resolve()]
    assert written[0][1].parent == result.absolute_path.parent
    assert written[0][1].name.startswith("episode_1_preview_")

    resolved = resolve_episode_preview(project_name="proj", episode=1, project_manager=pm)
    assert resolved is not None
    assert resolved.absolute_path == result.absolute_path
    assert resolved.subtitles_webvtt is not None
    assert resolved.subtitles[0].text == "机械字幕"


def test_compose_storyboard_episode_preview(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "videos").mkdir()
    clip = project_dir / "videos" / "E1S01.mp4"
    clip.write_bytes(b"clip")
    (project_dir / "project.json").write_text(
        '{"name":"proj","generation_mode":"storyboard","content_mode":"drama",'
        '"episodes":[{"episode":1,"script_file":"scripts/episode_1.json"}]}',
        encoding="utf-8",
    )
    scripts = project_dir / "scripts"
    scripts.mkdir()
    (scripts / "episode_1.json").write_text(
        '{"content_mode":"drama","scenes":[{"scene_id":"E1S01","duration_seconds":5,'
        '"generated_assets":{"video_clip":"videos/E1S01.mp4"}}]}',
        encoding="utf-8",
    )

    written: list[list[Path]] = []

    def fake_concat(paths: list[Path], output: Path) -> None:
        written.append(paths)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"preview")

    from lib.project_manager import ProjectManager

    result = compose_episode_preview(
        project_name="proj",
        episode=1,
        project_manager=ProjectManager(tmp_path),
        concatenate=fake_concat,
    )

    assert result.relative_path == "output/episode_1_preview.mp4"
    assert result.absolute_path.read_bytes() == b"preview"
    assert written[0] == [clip.resolve()]


def test_resolve_preview_missing(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "project.json").write_text('{"name":"proj"}', encoding="utf-8")
    from lib.project_manager import ProjectManager

    assert resolve_episode_preview(project_name="proj", episode=2, project_manager=ProjectManager(tmp_path)) is None


def _track(
    *,
    artifact: str,
    gain: float,
    duration_us: int = 5_000_000,
    audio_enabled: bool = True,
    start_us: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        media=SimpleNamespace(artifact_path=artifact),
        gain=gain,
        audio_enabled=audio_enabled,
        start_microseconds=start_us,
        duration_microseconds=duration_us,
    )


def test_presentation_mix_specs_resolve_paths_and_gains(tmp_path: Path) -> None:
    (tmp_path / "reference_videos").mkdir()
    (tmp_path / "audio").mkdir()
    video = tmp_path / "reference_videos" / "E1U01.mp4"
    tts = tmp_path / "audio" / "segment_E1U01.wav"
    video.write_bytes(b"v")
    tts.write_bytes(b"a")
    presentations = [
        SimpleNamespace(
            video=_track(artifact="reference_videos/E1U01.mp4", gain=1.0),
            narration_audio=_track(artifact="audio/segment_E1U01.wav", gain=0.4),
        ),
        SimpleNamespace(
            video=_track(artifact="reference_videos/E1U01.mp4", gain=1.0),
            narration_audio=None,
        ),
    ]

    specs = presentation_mix_specs(tmp_path, presentations)

    assert specs == [
        PresentationMixSpec(
            video_path=video.resolve(),
            video_gain=1.0,
            narration_path=tts.resolve(),
            narration_gain=0.4,
            narration_start_microseconds=0,
        ),
        PresentationMixSpec(
            video_path=video.resolve(),
            video_gain=1.0,
            narration_path=None,
            narration_gain=None,
            narration_start_microseconds=0,
        ),
    ]


def test_materialize_presentation_mix_clips_passthrough_full_gain_without_tts(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"clip")
    work = tmp_path / "work"
    work.mkdir()
    rendered: list[PresentationMixSpec] = []

    def fake_render(spec: PresentationMixSpec, output: Path) -> None:
        rendered.append(spec)
        output.write_bytes(b"mixed")

    paths = materialize_presentation_mix_clips(
        [
            PresentationMixSpec(video_path=clip, video_gain=1.0),
            PresentationMixSpec(
                video_path=clip,
                video_gain=1.0,
                narration_path=tmp_path / "t.wav",
                narration_gain=0.4,
            ),
        ],
        work_dir=work,
        render=fake_render,
    )

    assert paths[0] == clip.resolve()
    assert paths[1] == (work / "mix_0001.mp4").resolve()
    assert paths[1].read_bytes() == b"mixed"
    assert len(rendered) == 1
    assert rendered[0].narration_gain == 0.4


def test_compose_bakes_tts_mix_before_concat(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "reference_videos").mkdir()
    (project_dir / "audio").mkdir()
    clip = project_dir / "reference_videos" / "E1U01.mp4"
    tts = project_dir / "audio" / "segment_E1U01.wav"
    clip.write_bytes(b"clip")
    tts.write_bytes(b"tts")
    (project_dir / "project.json").write_text(
        '{"name":"proj","generation_mode":"reference_video","episodes":[{"episode":1,"script_file":"scripts/episode_1.json"}]}',
        encoding="utf-8",
    )
    scripts = project_dir / "scripts"
    scripts.mkdir()
    (scripts / "episode_1.json").write_text(
        '{"video_units":[{"unit_id":"E1U01","duration_seconds":5,"generated_assets":{"video_clip":"reference_videos/E1U01.mp4","narration_audio":"audio/segment_E1U01.wav"}}]}',
        encoding="utf-8",
    )

    written: list[tuple[list[Path], Path]] = []
    rendered: list[tuple[PresentationMixSpec, Path]] = []

    def fake_concat(paths: list[Path], output: Path) -> None:
        written.append((paths, output))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"preview")

    def fake_render(spec: PresentationMixSpec, output: Path) -> None:
        rendered.append((spec, output))
        output.write_bytes(b"mixed")

    from lib.project_manager import ProjectManager

    presentations = [
        SimpleNamespace(
            video=_track(artifact="reference_videos/E1U01.mp4", gain=1.0),
            narration_audio=_track(artifact="audio/segment_E1U01.wav", gain=0.4),
            subtitles=(),
        )
    ]
    result = compose_reference_episode_preview(
        project_name="proj",
        episode=1,
        project_manager=ProjectManager(tmp_path),
        concatenate=fake_concat,
        render_mix=fake_render,
        presentations=presentations,
    )

    assert result.absolute_path.read_bytes() == b"preview"
    assert len(rendered) == 1
    assert rendered[0][0].video_gain == 1.0
    assert rendered[0][0].narration_gain == 0.4
    assert written[0][0] == [rendered[0][1].resolve()]
