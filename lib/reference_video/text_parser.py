"""参考生视频正文解析器：书写层记号（``@[名称]`` 引用、台词与画外音）的识别原语。

视频单元只持久化正文，参考图与发声归属都由本模块从正文读时派生（见 ADR 0064）：解析结果
不落盘，改正文即改一切派生物。"""

from __future__ import annotations

import unicodedata
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Literal

from lib.asset_types import BUCKET_KEY, asset_name_comparison_key, normalize_asset_bucket
from lib.character_looks import format_mention, look_is_registered, split_mention_inner
from lib.script_models import ReferenceResource

#: BOM / ZWNBSP。前端按 JS 的 ``\s`` 判行首空白，U+FEFF 属之；Python 的 ``str.strip()``
#: 不认它（``"﻿".isspace()`` 为 False）。不归一会让带 BOM 的记号在前端认、
#: 在后端不认——同一份正文在编辑器与执行期派生出不同的参考图。
#: BOM 在正文里没有语义，解析入口一次性去掉，两条派生路径回到同一口径。
_BOM = "﻿"

#: 说话人 mention 与 ``{`` 之间允许出现的分隔冒号（中英各一）。只允许一个：写了第二个
#: 就说明这不是「``@[角色]：{台词}``」的形态，此时宁可不成记号，也不静默降级成画外音。
_SPEAKER_SEPARATORS = "：:"

#: 内心独白标记：写在说话人 mention 内层末尾 ``@[角色%内心独白]{…}``。
#: 结构标记固定为中文关键字（与 ``@造型`` 的 ``@`` 分隔符错开），多语言项目同样逐字使用。
INNER_MONOLOGUE_SEP = "%"
INNER_MONOLOGUE_TAG = "内心独白"
INNER_MONOLOGUE_SUFFIX = f"{INNER_MONOLOGUE_SEP}{INNER_MONOLOGUE_TAG}"

SpeechKind = Literal["dialogue", "voiceover", "inner_monologue"]


def _normalize_source(text: str) -> str:
    """书写层文本的入口归一：去掉全部 U+FEFF，并把编码形式收敛到 Unicode NFC。

    两者同一性质——屏幕上看不见的字节差异，却让按字节走的判定分叉，故合并在一个入口处理。
    BOM 不止出现在文档开头：粘贴拼接会把它带到任意行首，而分叉是按行发生的。NFC 则是
    资产名比对的坐标系（见 :func:`lib.asset_types.asset_name_comparison_key`）：说话人位与
    ``@[名称]`` 引用都要与资产表的 key 判等，正文以 NFD 落盘、资产表以 NFC 登记时两者
    肉眼同字却判不相等。

    归一落在三个行级原语（``split_speech_line`` / ``leading_mention_before_colon`` /
    ``find_malformed_mention``）上——它们各自与前端同名函数互为镜像，单独调用时也须同判。
    名字提取出口再经比对 helper 去除两端空白；因此说话人名与 mention 名一律已 strip + NFC，
    台词文本已是 NFC。
    """
    stripped = text.replace(_BOM, "") if _BOM in text else text
    return unicodedata.normalize("NFC", stripped)


def _is_ascii_word_char(ch: str) -> bool:
    return ch == "_" or (ch.isascii() and ch.isalnum())


def _is_legacy_mention_char(ch: str) -> bool:
    return ch == "_" or (ch.isascii() and ch.isalnum()) or ("\u4e00" <= ch <= "\u9fff")


def _next_positions(text: str, targets: set[str]) -> list[int]:
    next_pos = [len(text)] * (len(text) + 1)
    for i in range(len(text) - 1, -1, -1):
        next_pos[i] = i if text[i] in targets else next_pos[i + 1]
    return next_pos


def _iter_mentions(text: str) -> Iterator[tuple[int, int, str]]:
    """Yield (start, end, name) for @名称 / @[名称] mentions.

    The left side of `@` must not be an ASCII word character, otherwise the text
    is treated as an email/id fragment. Wrapped mentions may contain punctuation
    but cannot cross line breaks. Curly-brace wrapping is intentionally excluded
    because the editor only writes `@[名称]` and the runtime contract stays on a
    single wrapped form.
    """
    next_square = _next_positions(text, {"]"})
    next_line_break = _next_positions(text, {"\r", "\n"})
    i = 0
    while i < len(text):
        if text[i] != "@":
            i += 1
            continue

        if i > 0 and _is_ascii_word_char(text[i - 1]):
            i += 1
            continue

        if i + 1 >= len(text):
            i += 1
            continue

        opener = text[i + 1]
        if opener == "[":
            start = i + 2
            close = next_square[start]
            if start < close < next_line_break[start]:
                yield i, close + 1, text[start:close]
                i = close + 1
                continue
            i += 1
            continue

        j = i + 1
        while j < len(text) and _is_legacy_mention_char(text[j]):
            j += 1
        if j > i + 1:
            yield i, j, text[i + 1 : j]
            i = j
            continue
        i += 1


@dataclass(frozen=True)
class ParsedMention:
    """画面引用 mention：资产名 + 可选角色造型（``None`` = 基准）。"""

    name: str
    look: str | None = None

    @property
    def dedupe_key(self) -> tuple[str, str]:
        return self.name, self.look or ""


def _parse_mention_token(raw: str) -> ParsedMention:
    name, look = split_mention_inner(_strip_inner_monologue_suffix(raw)[0])
    return ParsedMention(name=name, look=look)


def _strip_inner_monologue_suffix(raw: str) -> tuple[str, bool]:
    """剥掉内层末尾的 ``%内心独白``；返回 ``(剩余内层, 是否内心独白)``。"""
    text = raw if isinstance(raw, str) else ""
    # 入口已 NFC；此处再归一一次，避免单独调用时 NFD 后缀对不上。
    text = unicodedata.normalize("NFC", text)
    suffix = unicodedata.normalize("NFC", INNER_MONOLOGUE_SUFFIX)
    if text.endswith(suffix):
        return text[: -len(suffix)], True
    return text, False


def parse_speech_speaker_token(raw: str) -> tuple[str, SpeechKind] | None:
    """解析说话人 mention 内层：``(角色名, kind)``；非法 ``%`` 标记返回 ``None``。

    - 无 ``%``：dialogue
    - 以 ``%内心独白`` 结尾：inner_monologue（可与 ``角色@造型`` 同写，音色仍挂角色名）
    - 含其它 ``%…``：不成说话人（调用方不降级成画外音）
    """
    remainder, is_monologue = _strip_inner_monologue_suffix(raw)
    if not is_monologue and INNER_MONOLOGUE_SEP in remainder:
        return None
    name, _look = split_mention_inner(remainder)
    if not name:
        return None
    kind: SpeechKind = "inner_monologue" if is_monologue else "dialogue"
    return name, kind


@dataclass(frozen=True)
class SpeechMark:
    """行内一段发声记号的解析结果。

    ``kind``：
    - ``voiceover``：裸 ``{台词}``（旁白画外音，无说话人）
    - ``dialogue``：``@[角色]{台词}``（开口说，出镜时可口型）
    - ``inner_monologue``：``@[角色%内心独白]{台词}``（角色音色心声，出镜亦闭嘴）

    ``speaker`` 在 voiceover 时为空串；名称已归一到资产名比对坐标系。``text`` 是花括号内的逐字台词。

    ``raw`` 是该记号在归一后原行里占据的整段原文（含说话人 mention 与其后的空白 / 冒号）：
    「从不删字」的渲染路径（未登记说话人）据此原样回填，无须再回原行按偏移取。
    """

    speaker: str
    text: str
    raw: str
    kind: SpeechKind = "voiceover"

    def __post_init__(self) -> None:
        if self.kind == "voiceover":
            if self.speaker:
                raise ValueError("voiceover SpeechMark must have empty speaker")
        elif not self.speaker:
            raise ValueError(f"{self.kind} SpeechMark requires a speaker")


def split_speech_line(line: str) -> list[str | SpeechMark]:
    """把一行拆成「画面描述片段」与「发声记号」的有序序列（记号可出现在行内任意位置）。

    发声记号三种，语言无关结构：
    - ``{台词}`` → 画外音（旁白）
    - ``@[角色]{台词}``（mention 与花括号之间允许空白或一个中英冒号）→ 开口台词
    - ``@[角色%内心独白]{台词}`` → 内心独白（多角色各写一条即可）

    说话人只认「紧贴花括号之前的那个 mention」，不做「行内最近 mention 猜 speaker」式
    启发式——推断错误会把台词静默绑到错误角色的参考音频上。以下情形一律不成记号，花括号
    留在描述片段里，由调用侧按各自严格度出 warning 或判违约：

    - 空台词（``{}`` / ``{   }``）：``Utterance`` 要求 text 非空，放行会派生出没有内容的发声。
    - 说话人位为空白（``@[ ]{台词}``）：dialogue / 内心独白要求非空 speaker。
    - 说话人位写坏（``@[]{台词}``、``@[李明{台词}``、``@[张三]：：{台词}``）：花括号前是 ``]``
      或又一个分隔冒号、却没有可用 mention 时不降级成画外音——作者写的是「某人说」，静默改成
      画外音比不识别更难发现。
    - 说话人内层含非法 ``%`` 标记（不是末尾 ``%内心独白``）：同上，不成记号。

    拼接结果（描述片段 + 各记号的 ``raw``）逐字等于归一后的原行，故 ``strip_speech_marks``
    是无损切分的另一半，不会吞字。
    """
    text = _normalize_source(line)
    mentions = list(_iter_mentions(text))
    parts: list[str | SpeechMark] = []
    cursor = 0
    scan = 0
    while True:
        open_index = text.find("{", scan)
        if open_index < 0:
            break
        close_index = text.find("}", open_index + 1)
        if close_index < 0:
            break
        inner = text[open_index + 1 : close_index]
        if "{" in inner:
            # 嵌套 / 漏闭合：外层 ``{`` 不成记号，从内层重新扫描。
            scan = open_index + 1
            continue
        if not inner.strip():
            scan = close_index + 1
            continue

        head = open_index
        while head > cursor and text[head - 1].isspace():
            head -= 1
        separator_colon = False
        if head > cursor and text[head - 1] in _SPEAKER_SEPARATORS:
            head -= 1
            separator_colon = True
            while head > cursor and text[head - 1].isspace():
                head -= 1

        speaker = ""
        kind: SpeechKind = "voiceover"
        start = open_index
        mention = next((m for m in mentions if m[1] == head and m[0] >= cursor), None)
        if mention is not None:
            parsed = parse_speech_speaker_token(mention[2])
            if parsed is None:
                scan = close_index + 1
                continue
            speaker, kind = parsed
            start = mention[0]
        elif head > cursor and (text[head - 1] == "]" or (separator_colon and text[head - 1] in _SPEAKER_SEPARATORS)):
            scan = close_index + 1
            continue

        if start > cursor:
            parts.append(text[cursor:start])
        parts.append(SpeechMark(speaker=speaker, text=inner, raw=text[start : close_index + 1], kind=kind))
        cursor = close_index + 1
        scan = cursor

    if cursor < len(text):
        parts.append(text[cursor:])
    return parts


def line_speech_marks(line: str) -> list[SpeechMark]:
    """一行里按出现顺序排列的发声记号。"""
    return [part for part in split_speech_line(line) if isinstance(part, SpeechMark)]


def speech_line_description(parts: Iterable[str | SpeechMark]) -> str:
    """``split_speech_line`` 结果里记号之外的残余文本，即这一行的画面描述。

    收在此处而不是各调用侧就地 join：同时要记号与残余的调用方（预览派生、发声准入、草稿
    校验）只能切一次再各取一半，否则「什么算描述」会在三处各写一遍、日后随记号语法一起漂移。
    """
    return "".join(part for part in parts if isinstance(part, str))


def strip_speech_marks(line: str) -> str:
    """去掉全部发声记号后剩下的画面描述文本（归一形）。

    参考图派生与产物依据都按此文本判定：只在花括号前出现的角色只绑声音、不进画面参考，
    而同一行里写在记号之外的 ``@[名称]`` 照常进参考图。
    """
    return speech_line_description(split_speech_line(line))


def leading_mention_before_colon(line: str) -> str | None:
    """行首为 ``@[名称]：`` 形态时返回该名称，否则返回 ``None``（只看名称与冒号，不判花括号）。

    与「这行首是不是一个发声记号」的严判之差即「本想写台词但写坏了」：漏花括号、花括号不
    成对、说话人位空白。机器产物校验据此把这类行判违约，而不是让它以画面描述的身份放行
    （说话人会被派生成参考图、台词则整句消失）。返回名称而不是布尔，是因为这一形态还要看
    名称是不是角色——场景 / 道具做小标题（``@[酒馆]：木门被风吹开``）是合法的画面描述写法，
    不能与漏花括号的台词混为一谈。
    """
    stripped = _normalize_source(line).strip()
    if not stripped.startswith("@"):
        return None
    first = next(_iter_mentions(stripped), None)
    if first is None or first[0] != 0:
        return None
    rest = stripped[first[1] :].lstrip()
    if not rest or rest[0] not in "：:":
        return None
    return _parse_mention_token(first[2]).name


def find_malformed_mention(line: str) -> str | None:
    """返回行内首个写坏的 ``@[`` 引用片段（如 ``@[李明`` / ``@[]``）；没有则返回 ``None``。

    ``_iter_mentions`` 对这类 token 静默不产出 mention，正文里的坏 token 因此既不进
    references，又会被 ``render_mentions_as_subjects`` 原样带进供应商请求（它只替换认得的
    mention、从不删字）。左侧是 ASCII 词字符时按邮箱 / id 片段跳过，与 ``_iter_mentions`` 同口径。

    全角形（``＠[李明]`` / ``@［李明］``）一并算坏 token：中文输入法下模型很容易写出，而语法只认
    半角，静默放行的后果同样是那张参考图从视频请求里消失。
    """
    text = _normalize_source(line)
    for index, char in enumerate(text):
        if char == "＠" or (char == "@" and text[index + 1 : index + 2] == "［"):
            return text[index : index + 20]
    starts = {start for start, _end, _name in _iter_mentions(text)}
    for index in range(len(text) - 1):
        if text[index] != "@" or text[index + 1] != "[" or index in starts:
            continue
        if index > 0 and _is_ascii_word_char(text[index - 1]):
            continue
        return text[index : index + 20]
    return None


def extract_mention_refs(text: str) -> list[ParsedMention]:
    """提取正文中的画面 ``@`` 引用（含造型），保持首次出现顺序、按 (名, look) 去重。

    发声记号内的说话人位不计入（与 :func:`extract_mentions` 同口径）。
    """
    seen: set[tuple[str, str]] = set()
    result: list[ParsedMention] = []
    for raw_line in text.splitlines():
        line = strip_speech_marks(raw_line)
        for _start, _end, raw_name in _iter_mentions(line):
            mention = _parse_mention_token(raw_name)
            if not mention.name:
                continue
            key = mention.dedupe_key
            if key not in seen:
                seen.add(key)
                result.append(mention)
    return result


def extract_mentions(text: str) -> list[str]:
    """提取正文中的 ``@`` 引用**资产名**（保持首次出现顺序、去重）。

    造型后缀不进入本列表——调用方若只需「提到了哪些资产」用本函数；需要造型选型时用
    :func:`extract_mention_refs`。顺序即执行期参考图编号顺序的资产维；同一角色不同造型
    在参考图列表里是多条，见 :func:`derive_references_from_text`。

    **发声记号内的说话人位不计入**：给画外说话的角色附参考图会诱导模型把他画进画面。
    """
    seen: set[str] = set()
    result: list[str] = []
    for mention in extract_mention_refs(text):
        if mention.name not in seen:
            seen.add(mention.name)
            result.append(mention.name)
    return result


def rewrite_mentions(text: str, old_name: str, new_name: str) -> tuple[str, int]:
    """把文本中指向 *old_name* 的 @ 引用改写为 ``@[new_name]`` / ``@[new_name@look]``。

    资产重命名的正文改写原语：与 ``_iter_mentions`` 同口径识别 mention（含旧式裸 ``@名字``，
    改写时一并升格为包裹形式），名字判等走比对坐标系（NFC）；``@[旧@战损]`` 保留造型后缀。
    不做其他归一（不去 BOM、不整体 NFC），未命中的字符原样保留。
    """
    target = asset_name_comparison_key(old_name)
    pieces: list[str] = []
    last = 0
    count = 0
    for start, end, raw in _iter_mentions(text):
        mention = _parse_mention_token(raw)
        if mention.name != target:
            continue
        replacement = f"@[{format_mention(new_name, mention.look)}]"
        if text[start:end] == replacement:
            continue
        pieces.append(text[last:start])
        pieces.append(replacement)
        last = end
        count += 1
    if not count:
        return text, 0
    pieces.append(text[last:])
    return "".join(pieces), count


def derive_references_from_text(text: str, project: dict) -> tuple[list[ReferenceResource], list[str]]:
    """书写层正文 → ``(references, missing)`` 的唯一派生入口。

    参考图是纯派生物、不落盘：机器产物校验、编辑器预览与执行期请求投影都经本函数从同一份
    正文派生。``missing`` 含未登记资产名，以及「角色已登记但造型未登记」时的 ``角色@造型`` 键。
    """
    return resolve_references(extract_mention_refs(text), project)


def render_mentions_as_subjects(text: str, names: Collection[str]) -> str:
    """把 prompt 中的 ``@[X]`` / ``@[X@look]`` / ``@X`` 替换为三段论的主体记号 ``<X>``。

    ``names`` 是资产表里已登记的名字——``<X>`` 是画面主体记号、不指向参考图编号；造型后缀
    在主体记号里去掉（同一角色不同造型仍是同一主体名）。不在其中的 mention 原样保留、从不删字。
    """
    normalized_names = {asset_name_comparison_key(name) for name in names}
    text = _normalize_source(text)
    parts: list[str] = []
    last = 0
    for start, end, raw in _iter_mentions(text):
        parts.append(text[last:start])
        mention = _parse_mention_token(raw)
        parts.append(f"<{mention.name}>" if mention.name in normalized_names else text[start:end])
        last = end

    parts.append(text[last:])
    return "".join(parts)


def resolve_references(
    mentions: list[ParsedMention] | list[str],
    project: dict,
) -> tuple[list[ReferenceResource], list[str]]:
    """按 project.json 四类资产把 mention 分派成 ReferenceResource。

    入参可以是 :class:`ParsedMention` 列表或旧式纯名字列表（后者一律视为基准造型）。
    角色带未登记造型时：仍产出 character 引用（look 填入），同时把 ``角色@造型`` 记入 missing，
    供机器校验拒盘 / 人写路径警告。
    """
    buckets: dict[str, dict[str, Any]] = {
        "product": normalize_asset_bucket(project.get(BUCKET_KEY["product"])),
        "character": normalize_asset_bucket(project.get(BUCKET_KEY["character"])),
        "scene": normalize_asset_bucket(project.get(BUCKET_KEY["scene"])),
        "prop": normalize_asset_bucket(project.get(BUCKET_KEY["prop"])),
    }
    refs: list[ReferenceResource] = []
    missing: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    normalized: list[ParsedMention] = []
    for item in mentions:
        if isinstance(item, ParsedMention):
            normalized.append(item)
        else:
            normalized.append(ParsedMention(name=asset_name_comparison_key(item), look=None))

    for mention in normalized:
        name = mention.name
        look = mention.look
        dedupe = (name, look or "", "")
        match = next((rtype for rtype, bucket in buckets.items() if name in bucket), None)
        if match is None:
            miss_key = format_mention(name, look) if look else name
            if miss_key not in missing:
                missing.append(miss_key)
            continue
        if look is not None and match != "character":
            miss_key = format_mention(name, look)
            if miss_key not in missing:
                missing.append(miss_key)
            look = None
        if match == "character" and look is not None:
            entry = buckets["character"].get(name)
            if isinstance(entry, dict) and not look_is_registered(entry, look):
                miss_key = format_mention(name, look)
                if miss_key not in missing:
                    missing.append(miss_key)
                # 仍带 look 产出，执行期选图会失败并走 missing 资产；机器校验用 missing 拒盘
        dedupe = (match, name, look or "")
        if dedupe in seen:
            continue
        seen.add(dedupe)
        refs.append(ReferenceResource(type=match, name=name, look=look))  # type: ignore[arg-type]
    return refs, missing
