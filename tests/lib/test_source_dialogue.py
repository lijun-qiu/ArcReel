"""源文行首对白的抽出，以及拆分前把未登记说话人写入角色表。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.project_manager import ProjectManager
from lib.source_dialogue import extract_source_dialogues, register_source_speakers, source_speakers

pytestmark = pytest.mark.unit

_SOURCE = """\
## S01 | 外景 · 彩票中心门口 | 上午

工作人员：先生，您确定不办个VIP——

林北：（打断）全部转到这张卡里。

客户经理：全部？

林北：全部。

时间：上午
林北没有回头。他说：这句不在行首，不该被抽成说话人。
"""


def _project(tmp_path: Path) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata(
        "demo",
        "Demo",
        "",
        "drama",
        extras={"generation_mode": "reference_video", "grid_storyboard": False},
    )
    pm.upsert_assets("demo", "characters", {"林北": {"description": "短发青年", "voice_style": ""}})
    return pm


def test_extract_source_dialogues_keeps_speakers_and_drops_stage_directions() -> None:
    lines = extract_source_dialogues(_SOURCE)
    assert [(line.speaker, line.text) for line in lines] == [
        ("工作人员", "先生，您确定不办个VIP——"),
        ("林北", "全部转到这张卡里。"),
        ("客户经理", "全部？"),
        ("林北", "全部。"),
    ]
    assert source_speakers(_SOURCE) == ["工作人员", "林北", "客户经理"]


def test_extract_ignores_prose_without_leading_speaker() -> None:
    assert extract_source_dialogues("五百万只是起步。他要囤百亿。") == []


def test_extract_ignores_cast_list_人物_header() -> None:
    """分场「人物：…」是出演名单，不能抽成说话人「人物」的假对白。"""
    source = """\
## S01 | 外景 · 废墟 | 黄昏
人物：林北、黑蛇、林小雨、赵猛

黑蛇：林北，你以为你很能打？

## S02 | 内景 · 卧室 | 清晨
人物：林北

林北：不是梦。
"""
    lines = extract_source_dialogues(source)
    assert [(line.speaker, line.text) for line in lines] == [
        ("黑蛇", "林北，你以为你很能打？"),
        ("林北", "不是梦。"),
    ]
    assert "人物" not in source_speakers(source)


def test_extract_skips_voiceover_and_name_lists() -> None:
    """旁白 / 画外音（含变体）不登记为角色；顿号人名名单即使标签不认识也不当对白。"""
    source = """\
旁白：这一次，谁都不能死。
画外音：雨还在下。
旁白（女）：哥。
在场：林北、赵猛
林北：不是梦。
"""
    lines = extract_source_dialogues(source)
    assert [(line.speaker, line.text) for line in lines] == [("林北", "不是梦。")]
    speakers = source_speakers(source)
    assert speakers == ["林北"]
    assert "旁白" not in speakers
    assert "画外音" not in speakers
    assert "旁白（女）" not in speakers


def test_extract_speaker_心里话_as_inner_monologue_on_character() -> None:
    """``阿杰（心里话）：…`` 抽出说话人阿杰 + inner_monologue，不登记假角色名。"""
    source = """\
阿杰（心里话）：男生的遗憾，是从一枚奇趣蛋开始的。
阿杰：我想吃这个。
阿杰（内心独白）：似乎降低自己的欲望感，失望就会少一点。
阿杰(心声)：算了，下个月吧。
"""
    lines = extract_source_dialogues(source)
    assert [(line.kind, line.speaker, line.text) for line in lines] == [
        ("inner_monologue", "阿杰", "男生的遗憾，是从一枚奇趣蛋开始的。"),
        ("dialogue", "阿杰", "我想吃这个。"),
        ("inner_monologue", "阿杰", "似乎降低自己的欲望感，失望就会少一点。"),
        ("inner_monologue", "阿杰", "算了，下个月吧。"),
    ]
    assert source_speakers(source) == ["阿杰"]


def test_extract_rejects_stage_direction_and_prop_like_speakers() -> None:
    """舞台提示 / 物件名不当说话人，也不进角色表。

    例：「厨房外面，母亲的声音远远传来：…」「母亲的字迹：…」——冒号前不是可立绘角色名。
    """
    source = """\
厨房外面，母亲的声音远远传来：闺女，药熬好了。
母亲的字迹：记得按时吃药。
母亲（声音从门外传来）：开门。
门外传来母亲的声音：快来。
母亲：我回来了。
"""
    lines = extract_source_dialogues(source)
    assert [(line.speaker, line.text) for line in lines] == [("母亲", "我回来了。")]
    assert source_speakers(source) == ["母亲"]


def test_register_source_speakers_skips_stage_direction_speakers(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    source = """\
厨房外面，母亲的声音远远传来：闺女。
母亲的字迹：记得吃药。
客户经理：全部？
"""
    added = register_source_speakers(pm, "demo", source)
    assert added == ("客户经理",)
    characters = pm.load_project("demo")["characters"]
    assert "客户经理" in characters
    assert "厨房外面，母亲的声音远远传来" not in characters
    assert "母亲的字迹" not in characters


def test_extract_strips_narrative_speech_verbs_and_rejects_action_attributions() -> None:
    """叙述体「女儿说：“…”」不得登记假角色「女儿说」；动作归因整行丢弃。"""
    source = """\
女儿说：“妈，您别翻了，我帮您找。”
母亲说：“我自己找。”
女儿问：“里面是什么？”
老师傅说道：“送人？”
女儿心里一沉：“就是您的。”
母亲摇摇头：“这个没有。”
母亲：我回来了。
"""
    lines = extract_source_dialogues(source)
    assert [(line.speaker, line.text) for line in lines] == [
        ("女儿", "“妈，您别翻了，我帮您找。”"),
        ("母亲", "“我自己找。”"),
        ("女儿", "“里面是什么？”"),
        ("老师傅", "“送人？”"),
        ("母亲", "我回来了。"),
    ]
    assert source_speakers(source) == ["女儿", "母亲", "老师傅"]
    assert "女儿说" not in source_speakers(source)
    assert "母亲说" not in source_speakers(source)
    assert "女儿问" not in source_speakers(source)
    assert "女儿心里一沉" not in source_speakers(source)
    assert "母亲摇摇头" not in source_speakers(source)


def test_register_source_speakers_does_not_add_narrative_attribution_names(tmp_path: Path) -> None:
    """药盒式叙述体拆分前登记，不得写入「女儿说 / 母亲问」假角色。"""
    pm = _project(tmp_path)
    pm.upsert_assets(
        "demo",
        "characters",
        {
            "母亲": {"description": "老年女性", "voice_style": ""},
            "女儿": {"description": "中年女性", "voice_style": ""},
        },
    )
    source = """\
女儿说：“妈，您别翻了，我帮您找。”
母亲说：“我自己找。”
女儿问：“里面是什么？”
女儿心里一沉：“就是您的，您看，绿色的，牡丹花。”
母亲摇摇头：“我的那个，花瓣上有一道划痕。”
女儿说：“我是您女儿。”
"""
    added = register_source_speakers(pm, "demo", source)
    assert added == ()
    characters = pm.load_project("demo")["characters"]
    assert set(characters) == {"林北", "母亲", "女儿"}
    for bogus in ("女儿说", "母亲说", "女儿问", "女儿心里一沉", "母亲摇摇头"):
        assert bogus not in characters


def test_register_source_speakers_adds_missing_and_skips_existing(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    added = register_source_speakers(pm, "demo", _SOURCE)
    assert added == ("工作人员", "客户经理")
    characters = pm.load_project("demo")["characters"]
    assert "工作人员" in characters
    assert characters["工作人员"]["looks"]["base"]["description"]
    assert characters["林北"]["description"] == "短发青年"
    assert register_source_speakers(pm, "demo", _SOURCE) == ()


def test_register_source_speakers_rejects_name_owned_by_another_asset(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    pm.upsert_assets("demo", "scenes", {"收银员": {"description": "收银台", "scene_sheet": ""}})
    with pytest.raises(ValueError, match="无法登记为角色"):
        register_source_speakers(pm, "demo", "收银员：六万八千四百二十三元。")
    assert "收银员" not in pm.load_project("demo")["characters"]
