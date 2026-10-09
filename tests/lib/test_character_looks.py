"""角色衣橱 look：解析、迁移、改名与投影。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from lib.asset_rename import renamed_file_stem, rewrite_entry_paths, rewrite_payload_references
from lib.asset_types import ASSET_SPECS
from lib.character_looks import (
    BASE_LOOK_ID,
    ensure_character_looks,
    format_mention,
    look_asset_id,
    look_sheet_relpath,
    split_mention_inner,
    upsert_look,
)
from lib.project_migrations.v9_to_v10_character_looks import migrate_v9_to_v10
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.reference_video.request_projection import resolve_reference_assets
from lib.reference_video.text_parser import (
    extract_mention_refs,
    extract_mentions,
    rewrite_mentions,
    split_speech_line,
)


def test_split_mention_inner_base_and_look():
    assert split_mention_inner("林北") == ("林北", None)
    assert split_mention_inner("林北@base") == ("林北", None)
    assert split_mention_inner("林北@战损") == ("林北", "战损")


def test_extract_mention_refs_dedupes_looks_and_skips_speaker():
    text = "@[林北] 走进 @[林北@战损]。@[林北]{别动。}\n@[林北@战损] 又出现。"
    refs = extract_mention_refs(text)
    assert [(r.name, r.look) for r in refs] == [("林北", None), ("林北", "战损")]
    assert extract_mentions(text) == ["林北"]


def test_speech_speaker_strips_look_suffix():
    parts = split_speech_line("@[林北@战损]{站住！}")
    marks = [p for p in parts if not isinstance(p, str)]
    assert len(marks) == 1
    assert marks[0].speaker == "林北"


def test_rewrite_mentions_preserves_look():
    text, n = rewrite_mentions("@[旧名@战损] 与 @[旧名]", "旧名", "新名")
    assert n == 2
    assert text == "@[新名@战损] 与 @[新名]"


def test_resolve_reference_assets_picks_look_sheet(tmp_path: Path):
    project_path = tmp_path
    (project_path / "characters").mkdir()
    base = project_path / "characters" / "林北.png"
    look = project_path / "characters" / "林北__战损.png"
    base.write_bytes(b"base")
    look.write_bytes(b"look")
    project = {
        "characters": {
            "林北": {
                "description": "干净",
                "character_sheet": "characters/林北.png",
                "looks": {
                    "base": {"description": "干净", "character_sheet": "characters/林北.png"},
                    "战损": {"description": "破衣", "character_sheet": "characters/林北__战损.png"},
                },
            }
        },
        "scenes": {},
        "props": {},
        "products": {},
    }
    unit = {"text": "@[林北@战损] 站起。"}
    assets = resolve_reference_assets(project, project_path, unit)
    assert len(assets) == 1
    assert assets[0].path == look
    assert assets[0].reference.look == "战损"


def test_migrate_v9_to_v10_adds_base_look(tmp_path: Path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    project = {
        "schema_version": 9,
        "characters": {
            "甲": {
                "description": "少年",
                "character_sheet": "characters/甲.png",
                "reference_image": "",
            }
        },
    }
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    migrate_v9_to_v10(project_dir)
    loaded = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    assert loaded["schema_version"] == 10
    assert CURRENT_PROJECT_SCHEMA_VERSION == 10
    looks = loaded["characters"]["甲"]["looks"]
    assert BASE_LOOK_ID in looks
    assert looks["base"]["description"] == "少年"
    assert looks["base"]["character_sheet"] == "characters/甲.png"


def test_renamed_file_stem_look_and_rewrite_paths():
    assert renamed_file_stem("林北__战损", "林北", "林远") == "林远__战损"
    entry = {
        "character_sheet": "characters/林北.png",
        "looks": {
            "base": {"character_sheet": "characters/林北.png"},
            "战损": {"character_sheet": "characters/林北__战损.png"},
        },
    }
    n = rewrite_entry_paths(entry, ASSET_SPECS["character"], "林北", "林远")
    assert n >= 2
    assert entry["character_sheet"] == "characters/林远.png"
    assert entry["looks"]["战损"]["character_sheet"] == "characters/林远__战损.png"
    payload = {"video_units": [{"text": "@[林北@战损] 走。"}]}
    count = rewrite_payload_references(payload, "character", "林北", "林远")
    assert count == 1
    assert payload["video_units"][0]["text"] == "@[林远@战损] 走。"


def test_look_paths_and_upsert():
    assert look_sheet_relpath("林北") == "characters/林北.png"
    assert look_sheet_relpath("林北", "战损") == "characters/林北__战损.png"
    assert look_asset_id("林北", "战损") == "林北__战损"
    assert format_mention("林北", "战损") == "林北@战损"
    entry: dict = {"description": "基准", "character_sheet": ""}
    ensure_character_looks(entry)
    upsert_look(entry, "战损", description="破甲")
    assert "战损" in entry["looks"]
    assert entry["looks"]["战损"]["description"] == "破甲"


def test_unknown_look_listed_in_missing():
    from lib.reference_video.text_parser import derive_references_from_text

    project = {
        "characters": {
            "林北": {
                "description": "x",
                "character_sheet": "",
                "looks": {"base": {"description": "x", "character_sheet": ""}},
            }
        },
        "scenes": {},
        "props": {},
        "products": {},
    }
    refs, missing = derive_references_from_text("@[林北@不存在] 跑。", project)
    assert any(r.name == "林北" and r.look == "不存在" for r in refs)
    assert "林北@不存在" in missing
