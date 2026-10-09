"""从剧本体源文抽出「角色：台词」，并在拆分前把尚未登记的说话人写入角色表。

只认行首的说话人标记（``工作人员：……`` / ``林北:……``）。舞台提示括号
（``（打断）``）不属于台词正文。说话人后的 ``（心里话）`` / ``（内心独白）`` /
``（心声）`` 表示内心独白，抽出后挂在角色名上（kind=inner_monologue），不登记成
``阿杰（心里话）`` 这种假角色。小说叙述句里的冒号不在行首，不抽出。

叙述体归因（``女儿说：“…”`` / ``母亲问：“…”`` / ``老师傅说道：“…”``）会剥掉句末
说/问/道等动词再认角色名，避免登记 ``女儿说``；``女儿心里一沉：“…”`` 一类动作归因
整行丢弃。自动登记只补**明确可立绘的角色名**（短名 / 身份称谓）。舞台提示、画外声源
描述、物件名（``母亲的字迹`` / ``……的声音远远传来``）一律不抽、不登记——道具 / 场景由
资产分析或衣橱审查补，不从冒号行臆造。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from lib.asset_types import (
    ProjectAssetNameConflictError,
    asset_name_comparison_key,
    resolve_asset_key,
    validate_asset_name,
)

#: 行首说话人。排除句读与叙述标点；过长前缀多半是舞台提示 / 叙述，不当角色名。
#: 中文逗号 / 顿号等一旦出现，整行不当「角色：台词」（如「厨房外面，母亲的声音……：」）。
_SPEAKER_LINE = re.compile(r"^([^\s#|：:。！？.!?，、；;…·．]{1,12})[：:](.+)$")
_STAGE_DIRECTION = re.compile(r"^(?:（[^）]*）|\([^)]*\))\s*")
#: 说话人后缀：心里话 / 内心独白 / 心声 → 角色名剥离，kind=inner_monologue。
_INNER_MONOLOGUE_SPEAKER_SUFFIX = re.compile(
    r"^(?P<name>.+?)(?:（(?:心里话|内心独白|心声)）|\((?:心里话|内心独白|心声)\))$"
)
_TRAILING_PUNCT = "。！？!?…—～~"
#: 场记标签，不是角色。旁白 / 画外音走画外音记号，不进角色表。
_NOT_SPEAKERS = frozenset(
    {
        "时间",
        "地点",
        "场景",
        "内景",
        "外景",
        "备注",
        "注",
        "字幕",
        "人物",
        "角色",
        "登场",
        "出场",
        "出演",
        "登场人物",
        "出场人物",
        "人物表",
        "主演",
    }
)
#: 名字里带这些标记的都不是可立绘角色（``旁白`` / ``旁白（女）`` / ``画外音``）。
_VOICEOVER_SPEAKER = re.compile(r"旁白|画外音|画外")
#: 舞台声源 / 物件 / 方位叙述误入说话人位（``……的声音``、``母亲的字迹``、``门外传来``）。
_NON_PERSON_SPEAKER = re.compile(
    r"声音|字迹|身影|背影|脚步|气息|影子|传来|传进|"
    r"外面|里面|远处|近处|门外|窗|门口|背后|身后|"
    r"的信|的手|的照片|的纸条|的字|的笔迹"
)
#: 「X的Y」且 Y 不是人称身份时，多半是道具 / 声源，不是角色名。
_POSSESSIVE_OBJECT_SPEAKER = re.compile(r".+的(?:字迹|声音|身影|背影|脚步|气息|影子|信|手|照片|纸条|笔迹|脸|目光)$")
#: 叙述体归因动词：小说常写「女儿说：“…”」「母亲问：“…”」，冒号在动词后。
#: 剧本格式是「女儿：台词」；若把「女儿说」整段当说话人会登记假角色。
#: 先剥双字「说道 / 问道」等，再剥单字，避免 ``说道`` 只剥掉 ``道`` 后残留 ``说``。
_NARRATIVE_SPEECH_VERB_SUFFIX = re.compile(
    r"(?:说道|问道|答道|叫道|喊道|笑道|回道|讲道|说|问|道|喊|叫|答|回|讲|嚷|吼|念|吟)$"
)
#: 叙述动作归因（``女儿心里一沉：“…”`` / ``母亲摇摇头：“…”``），不是可立绘短名。
_NARRATIVE_ACTION_SPEAKER = re.compile(
    r"心里|摇摇|点点|笑了|愣了|沉默|抬起|低下|转过|走过去|一沉|一顿|一惊|"
    r"看了|瞥了|皱着|叹了|停了|站起|坐了|走了|冲进|递过"
)
#: 顿号串起来的人名名单（``林北、黑蛇``），不管行首标签是什么，都不是对白。
_NAME_LIST = re.compile(r"^[^。！？!?、]{1,12}(?:、[^。！？!?、]{1,12})+$")
_SPEAKER_DESCRIPTION = "源文对白中的说话人，按名字所暗示的身份表现为普通人，衣着日常。"

SourceSpeechKind = Literal["dialogue", "inner_monologue"]


@dataclass(frozen=True)
class SourceDialogue:
    """源文里的一句发声：说话人已过资产名闸，``text`` 已去掉行首舞台提示。

    ``kind``：``dialogue`` 开口对白；``inner_monologue`` 来自 ``角色（心里话）：…`` 一类行，
    落 unit 时应写成 ``@[角色%内心独白]{…}``。
    """

    speaker: str
    text: str
    kind: SourceSpeechKind = "dialogue"


def dialogue_key(text: str) -> str:
    """对白比对键：NFC、去空白、去舞台提示、去掉句末标点。

    句中标点保留，避免「全部」被「全部转到这张卡里」误判为同一句。
    """

    compact = re.sub(r"\s+", "", unicodedata.normalize("NFC", text))
    compact = re.sub(r"（[^）]*）", "", compact)
    compact = re.sub(r"\([^)]*\)", "", compact)
    return compact.strip(_TRAILING_PUNCT)


def _split_speaker_token(raw: str) -> tuple[str, SourceSpeechKind] | None:
    """解析说话人位：先剥 ``（心里话）`` 等后缀，再拒旁白 / 舞台提示 / 物件名。"""

    token = unicodedata.normalize("NFC", raw.strip())
    if not token:
        return None
    kind: SourceSpeechKind = "dialogue"
    mono = _INNER_MONOLOGUE_SPEAKER_SUFFIX.match(token)
    if mono is not None:
        token = mono.group("name").strip()
        kind = "inner_monologue"
    # 「女儿说：」「母亲问：」「老师傅说道：」→ 说话人是女儿 / 母亲 / 老师傅，不是「女儿说」。
    while True:
        stripped = _NARRATIVE_SPEECH_VERB_SUFFIX.sub("", token)
        if stripped == token:
            break
        token = stripped.strip()
    if not token or _is_non_character_speaker(token):
        return None
    # 心里话后缀已剥掉；残留括号多半是舞台说明（``母亲（声音从门外传来）``）。
    if "（" in token or "(" in token:
        return None
    # 「女儿心里一沉：」「母亲摇摇头：」是叙述动作，不是角色短名。
    if _NARRATIVE_ACTION_SPEAKER.search(token) is not None:
        return None
    # 剥动词后若只剩动作残片（如单独「说」），不当角色。
    if _NARRATIVE_SPEECH_VERB_SUFFIX.fullmatch(token) is not None:
        return None
    try:
        speaker = validate_asset_name(token)
    except ValueError:
        return None
    if _is_non_character_speaker(speaker):
        return None
    return speaker, kind


def extract_source_dialogues(text: str) -> list[SourceDialogue]:
    """按出现顺序抽出源文对白 / 心里话；抽不出时返回空列表（叙述体源文不在此列）。"""

    found: list[SourceDialogue] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SPEAKER_LINE.match(line)
        if match is None:
            continue
        parsed = _split_speaker_token(match.group(1))
        if parsed is None:
            continue
        speaker, kind = parsed
        speech = match.group(2).strip()
        while True:
            stripped = _STAGE_DIRECTION.sub("", speech, count=1)
            if stripped == speech:
                break
            speech = stripped.strip()
        if not speech or not dialogue_key(speech) or _is_name_list(speech):
            continue
        found.append(SourceDialogue(speaker=speaker, text=speech, kind=kind))
    return found


def _is_non_character_speaker(speaker: str) -> bool:
    """场记、旁白 / 画外音、舞台声源与物件名，不登记为角色。"""

    if speaker in _NOT_SPEAKERS or _VOICEOVER_SPEAKER.search(speaker) is not None:
        return True
    if _NON_PERSON_SPEAKER.search(speaker) is not None:
        return True
    if _POSSESSIVE_OBJECT_SPEAKER.fullmatch(speaker) is not None:
        return True
    return False


def _is_name_list(speech: str) -> bool:
    """顿号分隔的人名名单不是对白。逗号保留：对白里的停顿也用逗号。"""

    return _NAME_LIST.fullmatch(re.sub(r"\s+", "", speech)) is not None


def source_speakers(text: str) -> list[str]:
    """源文对白说话人，保持首次出现顺序、按资产名比对键去重。"""

    seen: set[str] = set()
    names: list[str] = []
    for line in extract_source_dialogues(text):
        key = asset_name_comparison_key(line.speaker)
        if key in seen:
            continue
        seen.add(key)
        names.append(line.speaker)
    return names


class SpeakerProject(Protocol):
    """登记说话人所需的项目写入面：读项目、批量加角色。"""

    def load_project(self, project_name: str) -> dict[str, Any]: ...

    def add_characters_batch(self, project_name: str, characters: dict[str, dict[str, Any]]) -> int: ...


def register_source_speakers(pm: SpeakerProject, project_name: str, novel_text: str) -> tuple[str, ...]:
    """把源文里尚未登记的**明确角色说话人**写入 ``characters``，已有的跳过。

    仅补可立绘短名 / 身份称谓；舞台提示、声源描述、物件名不登记。返回本次新增的名字。
    名称已被场景 / 道具占用时整批不落盘并抛 ``ValueError``。
    """

    project = pm.load_project(project_name)
    characters = project.get("characters")
    if characters is None:
        characters = {}
    elif not isinstance(characters, dict):
        raise ValueError("characters must be an object")

    pending: list[str] = []
    for name in source_speakers(novel_text):
        if resolve_asset_key(characters, name) is None:
            pending.append(name)
    if not pending:
        return ()

    entries = {name: {"description": _SPEAKER_DESCRIPTION, "voice_style": ""} for name in pending}
    try:
        pm.add_characters_batch(project_name, entries)
    except ProjectAssetNameConflictError as exc:
        raise ValueError(f"源文说话人无法登记为角色：{exc}") from exc
    return tuple(pending)
