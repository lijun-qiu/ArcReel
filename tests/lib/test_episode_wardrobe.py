"""本集资产完备完成事实（造型 + 场景/道具）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.content_digest import prefixed_sha256_file
from lib.episode_wardrobe import (
    EpisodeWardrobeInvalidRequest,
    EpisodeWardrobeRevisionConflict,
    complete_episode_wardrobe,
    episode_source_revision,
)
from lib.project_manager import ProjectManager
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION

pytestmark = pytest.mark.unit


def _project(tmp_path: Path) -> tuple[ProjectManager, Path]:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata(
        "demo",
        "Demo",
        "",
        "drama",
        extras={"generation_mode": "reference_video", "grid_storyboard": False},
    )
    return pm, pm.get_project_path("demo")


def test_complete_episode_wardrobe_adds_look_and_skips_existing(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path)
    source = project_path / "source" / "episode_1.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("林北换上军大衣。", encoding="utf-8")
    revision = prefixed_sha256_file(source)
    pm.upsert_assets("demo", "characters", {"林北": {"description": "短发青年", "voice_style": ""}})

    first = complete_episode_wardrobe(
        pm,
        "demo",
        1,
        revision,
        {"林北": {"战损": "军大衣破损、右肩渗血"}},
    )
    assert first.added == ("林北@战损",)
    assert first.added_scenes == ()
    assert first.added_props == ()
    looks = pm.load_project("demo")["characters"]["林北"]["looks"]
    assert looks["战损"]["description"] == "军大衣破损、右肩渗血"

    second = complete_episode_wardrobe(
        pm,
        "demo",
        1,
        revision,
        {"林北": {"战损": "应被跳过的新描述"}},
    )
    assert second.added == ()
    looks = pm.load_project("demo")["characters"]["林北"]["looks"]
    assert looks["战损"]["description"] == "军大衣破损、右肩渗血"
    assert CURRENT_PROJECT_SCHEMA_VERSION >= 10


def test_complete_episode_wardrobe_registers_scenes_and_props_without_overwrite(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path)
    source = project_path / "source" / "episode_1.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("废墟里，卧室旁的洗手间。", encoding="utf-8")
    revision = prefixed_sha256_file(source)
    pm.upsert_assets("demo", "scenes", {"废墟": {"description": "旧描述", "scene_sheet": ""}})

    first = complete_episode_wardrobe(
        pm,
        "demo",
        1,
        revision,
        looks={},
        scenes={
            "废墟": "应被跳过",
            "卧室": "狭小出租屋卧室，窗帘紧闭",
            "洗手间": "狭小洗手间，镜面斑驳",
        },
        props={"军大衣": "旧军大衣，肩章磨损"},
    )
    assert first.added == ()
    assert first.added_scenes == ("卧室", "洗手间")
    assert first.added_props == ("军大衣",)
    project = pm.load_project("demo")
    assert project["scenes"]["废墟"]["description"] == "旧描述"
    assert project["scenes"]["卧室"]["description"] == "狭小出租屋卧室，窗帘紧闭"
    assert project["scenes"]["洗手间"]["scene_sheet"] == ""
    assert project["props"]["军大衣"]["description"] == "旧军大衣，肩章磨损"

    second = complete_episode_wardrobe(
        pm,
        "demo",
        1,
        revision,
        scenes={"卧室": "不应覆盖"},
        props={"军大衣": "不应覆盖"},
    )
    assert second.added_scenes == ()
    assert second.added_props == ()
    project = pm.load_project("demo")
    assert project["scenes"]["卧室"]["description"] == "狭小出租屋卧室，窗帘紧闭"
    assert project["props"]["军大衣"]["description"] == "旧军大衣，肩章磨损"


def test_complete_episode_wardrobe_rejects_stale_revision(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path)
    source = project_path / "source" / "episode_1.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("旧", encoding="utf-8")
    stale = episode_source_revision(project_path, 1)
    source.write_text("新", encoding="utf-8")
    with pytest.raises(EpisodeWardrobeRevisionConflict):
        complete_episode_wardrobe(pm, "demo", 1, stale, {})


def test_complete_episode_wardrobe_rejects_unknown_character(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path)
    source = project_path / "source" / "episode_1.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("正文", encoding="utf-8")
    revision = prefixed_sha256_file(source)
    with pytest.raises(EpisodeWardrobeInvalidRequest, match="not registered"):
        complete_episode_wardrobe(pm, "demo", 1, revision, {"路人": {"战损": "x"}})
