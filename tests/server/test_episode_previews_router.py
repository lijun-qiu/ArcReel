"""GET/POST /projects/{name}/episodes/{episode}/preview* for storyboard mode."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import episode_previews
from server.services.episode_preview_compose import EpisodePreviewResult
from tests.auth_deps import AUTH_DEPENDENCIES

pytestmark = pytest.mark.integration


def _client(monkeypatch, tmp_path: Path) -> tuple[TestClient, Path]:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir = projects_root / "proj"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(
        '{"name":"proj","generation_mode":"storyboard","content_mode":"drama",'
        '"episodes":[{"episode":1,"script_file":"scripts/episode_1.json","title":"EP1"}]}',
        encoding="utf-8",
    )
    scripts = project_dir / "scripts"
    scripts.mkdir()
    (scripts / "episode_1.json").write_text(
        '{"content_mode":"drama","scenes":[{"scene_id":"E1S01","duration_seconds":5,'
        '"generated_assets":{"video_clip":"videos/E1S01.mp4"}}]}',
        encoding="utf-8",
    )
    (project_dir / "videos").mkdir()
    (project_dir / "videos" / "E1S01.mp4").write_bytes(b"clip")

    pm = ProjectManager(projects_root)
    monkeypatch.setattr(episode_previews, "get_project_manager", lambda: pm)

    async def fake_materialize(project_name: str, episode: int):
        return ()

    monkeypatch.setattr(episode_previews, "_materialize_preview_presentations", fake_materialize)

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
        return EpisodePreviewResult(
            relative_path="output/episode_1_preview.mp4",
            absolute_path=out,
        )

    monkeypatch.setattr(
        "server.services.episode_preview_compose.compose_episode_preview",
        fake_compose,
    )

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(episode_previews.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="test", role="admin")
    return TestClient(app), project_dir


def test_storyboard_preview_compose_returns_file_url(monkeypatch, tmp_path: Path) -> None:
    client, _project_dir = _client(monkeypatch, tmp_path)

    response = client.post("/api/v1/projects/proj/episodes/1/preview-compose")

    assert response.status_code == 200
    body = response.json()
    assert body["relative_path"] == "output/episode_1_preview.mp4"
    assert body["url"] == "/api/v1/files/proj/output/episode_1_preview.mp4"


def test_storyboard_preview_get_reports_existing(monkeypatch, tmp_path: Path) -> None:
    client, project_dir = _client(monkeypatch, tmp_path)
    out = project_dir / "output" / "episode_1_preview.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"preview")

    response = client.get("/api/v1/projects/proj/episodes/1/preview")

    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is True
    assert body["url"] == "/api/v1/files/proj/output/episode_1_preview.mp4"
