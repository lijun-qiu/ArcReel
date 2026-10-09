"""POST /reference-videos/episodes/{episode}/preview-compose"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.project_manager import ProjectManager
from lib.speech_composition import SpeechOwner
from lib.speech_presentation import SubtitleCue
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import reference_videos
from server.services.episode_preview_compose import EpisodePreviewResult
from tests.auth_deps import AUTH_DEPENDENCIES

pytestmark = pytest.mark.integration


def _client(monkeypatch, tmp_path: Path) -> tuple[TestClient, Path]:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir = projects_root / "proj"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(
        '{"name":"proj","generation_mode":"reference_video","episodes":[{"episode":1,"script_file":"scripts/episode_1.json","title":"EP1"}]}',
        encoding="utf-8",
    )
    scripts = project_dir / "scripts"
    scripts.mkdir()
    (scripts / "episode_1.json").write_text(
        '{"video_units":[{"unit_id":"E1U01","duration_seconds":5,"generated_assets":{"video_clip":"reference_videos/E1U01.mp4"}}]}',
        encoding="utf-8",
    )
    (project_dir / "reference_videos").mkdir()
    (project_dir / "reference_videos" / "E1U01.mp4").write_bytes(b"clip")

    pm = ProjectManager(projects_root)
    monkeypatch.setattr(reference_videos, "get_project_manager", lambda: pm)

    async def fake_materialize(project_name: str, episode: int):
        return ()

    monkeypatch.setattr(reference_videos, "_materialize_preview_presentations", fake_materialize)

    def fake_compose(
        *,
        project_name: str,
        episode: int,
        project_manager: ProjectManager,
        concatenate=None,
        presentations=None,
    ):
        out = project_dir / "output" / "episode_1_preview.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"preview")
        cues = (SubtitleCue(0, 5_000_000, "机械字幕", SpeechOwner.NARRATOR, None),) if presentations else ()
        webvtt = "WEBVTT\n\n1\n00:00:00.000 --> 00:00:05.000\n机械字幕\n" if cues else None
        return EpisodePreviewResult(
            relative_path="output/episode_1_preview.mp4",
            absolute_path=out,
            subtitles_webvtt=webvtt,
            subtitles=cues,
        )

    monkeypatch.setattr(
        "server.services.episode_preview_compose.compose_reference_episode_preview",
        fake_compose,
    )

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(reference_videos.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="test", role="admin")
    return TestClient(app), project_dir


def test_preview_compose_returns_file_url(monkeypatch, tmp_path: Path) -> None:
    client, _project_dir = _client(monkeypatch, tmp_path)

    response = client.post("/api/v1/projects/proj/reference-videos/episodes/1/preview-compose")

    assert response.status_code == 200
    body = response.json()
    assert body["relative_path"] == "output/episode_1_preview.mp4"
    assert body["url"] == "/api/v1/files/proj/output/episode_1_preview.mp4"
    assert body["subtitles_webvtt"] is None
    assert body["subtitles"] == []


def test_preview_get_reports_existing_file(monkeypatch, tmp_path: Path) -> None:
    client, project_dir = _client(monkeypatch, tmp_path)
    out = project_dir / "output" / "episode_1_preview.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"preview")

    response = client.get("/api/v1/projects/proj/reference-videos/episodes/1/preview")

    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is True
    assert body["relative_path"] == "output/episode_1_preview.mp4"
    assert body["url"] == "/api/v1/files/proj/output/episode_1_preview.mp4"
    assert body["subtitles_webvtt"] is None
    assert body["subtitles"] == []
    assert body["upscaled_exists"] is False


def test_preview_get_reports_missing(monkeypatch, tmp_path: Path) -> None:
    client, _project_dir = _client(monkeypatch, tmp_path)

    response = client.get("/api/v1/projects/proj/reference-videos/episodes/1/preview")

    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is False
    assert body["relative_path"] is None
    assert body["url"] is None
    assert body["subtitles_webvtt"] is None
    assert body["subtitles"] == []
    assert body["upscaled_exists"] is False


def test_preview_compose_maps_missing_clips(monkeypatch, tmp_path: Path) -> None:
    client, _project_dir = _client(monkeypatch, tmp_path)

    from server.services import episode_preview_compose as svc

    def raise_missing(*, project_name, episode, project_manager, concatenate=None, presentations=None):
        raise svc.MissingUnitClipsError(["E1U01", "E1U02"])

    monkeypatch.setattr(svc, "compose_reference_episode_preview", raise_missing)

    response = client.post("/api/v1/projects/proj/reference-videos/episodes/1/preview-compose")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "E1U01" in detail
    assert "E1U02" in detail
