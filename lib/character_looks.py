"""角色衣橱 look：基准造型、路径约定与 entry 读写。

角色身份挂音色与名字；视觉可切换造型落在 ``looks`` 字典（见 ADR 0066）。
``base`` 必须存在且与顶层 ``description`` / ``character_sheet`` / ``reference_image`` 同源。
非 base 的 sheet 文件名为 ``characters/{角色名}__{look}.png``。
"""

from __future__ import annotations

from typing import Any

from lib.asset_types import (
    asset_name_comparison_key,
    normalize_asset_name,
    validate_asset_name,
)

BASE_LOOK_ID = "base"
LOOK_MENTION_SEP = "@"
LOOK_FILE_SEP = "__"


def validate_look_id(look: object) -> str:
    """校验 look 标识：与资产名同闸，且禁止 ``@`` / ``__``（后者是文件名分隔）。"""

    cleaned = validate_asset_name(look)
    if LOOK_MENTION_SEP in cleaned:
        raise ValueError("造型名不能包含 @")
    if LOOK_FILE_SEP in cleaned:
        raise ValueError("造型名不能包含 __")
    return cleaned


def normalize_look_id(look: str | None) -> str | None:
    """把 look 收敛到比对键；``None`` / 空 / ``base`` 一律表示基准（返回 ``None``）。"""

    if look is None:
        return None
    key = asset_name_comparison_key(look)
    if not key or key == BASE_LOOK_ID:
        return None
    return key


def split_mention_inner(raw: str) -> tuple[str, str | None]:
    """把 ``@[`` ``]`` 内层拆成 ``(资产名, look|None)``。

    仅按**第一个** ``@`` 分割：``林北@战损`` → ``(林北, 战损)``；无 ``@`` 则 look 为 ``None``。
    名称两侧空白在比对键上 strip；look 经 :func:`normalize_look_id`。
    """

    text = raw if isinstance(raw, str) else ""
    if LOOK_MENTION_SEP not in text:
        return asset_name_comparison_key(text), None
    name_part, look_part = text.split(LOOK_MENTION_SEP, 1)
    return asset_name_comparison_key(name_part), normalize_look_id(look_part)


def format_mention(name: str, look: str | None = None) -> str:
    """书写层 ``@[…]`` 内层：基准只写角色名，非基准写 ``角色@look``。"""

    effective = normalize_look_id(look)
    if effective is None:
        return name
    return f"{name}{LOOK_MENTION_SEP}{effective}"


def look_sheet_relpath(character_name: str, look: str | None = None) -> str:
    """角色造型 sheet 的约定相对路径（posix）。"""

    name = validate_asset_name(character_name)
    effective = normalize_look_id(look)
    if effective is None:
        return f"characters/{name}.png"
    look_id = validate_look_id(effective)
    return f"characters/{name}{LOOK_FILE_SEP}{look_id}.png"


def look_asset_id(character_name: str, look: str | None = None) -> str:
    """产物清单 / 生成任务用的资产 id：基准为角色名，非基准为 ``名__look``。"""

    name = asset_name_comparison_key(character_name)
    effective = normalize_look_id(look)
    if effective is None:
        return name
    return f"{name}{LOOK_FILE_SEP}{effective}"


def parse_look_asset_id(asset_id: str) -> tuple[str, str | None]:
    """``look_asset_id`` 的逆：``(角色名, look|None)``。"""

    key = asset_name_comparison_key(asset_id)
    if LOOK_FILE_SEP not in key:
        return key, None
    name, look = key.split(LOOK_FILE_SEP, 1)
    return name, normalize_look_id(look) or look


def ensure_character_looks(entry: dict[str, Any]) -> dict[str, Any]:
    """保证角色 entry 含 ``looks.base``，并与顶层 description / sheet / 原图对齐。

    就地改写并返回同一 dict。已有非 base look 保留；已有 base 时用顶层字段回填空位。
    """

    looks = entry.get("looks")
    if not isinstance(looks, dict):
        looks = {}
        entry["looks"] = looks
    base = looks.get(BASE_LOOK_ID)
    if not isinstance(base, dict):
        base = {}
        looks[BASE_LOOK_ID] = base
    description = entry.get("description")
    if (
        isinstance(description, str)
        and description
        and not (isinstance(base.get("description"), str) and base["description"])
    ):
        base["description"] = description
    elif "description" not in base:
        base["description"] = description if isinstance(description, str) else ""
    sheet = entry.get("character_sheet")
    if isinstance(sheet, str) and sheet:
        base["character_sheet"] = sheet
    elif not isinstance(base.get("character_sheet"), str):
        base["character_sheet"] = ""
    ref = entry.get("reference_image")
    if isinstance(ref, str) and ref:
        base["reference_image"] = ref
    elif "reference_image" not in base:
        base["reference_image"] = ""
    # 顶层与 base 同源：读旧路径仍有效
    if isinstance(base.get("description"), str) and base["description"]:
        entry["description"] = base["description"]
    if isinstance(base.get("character_sheet"), str):
        entry["character_sheet"] = base["character_sheet"]
    if isinstance(base.get("reference_image"), str):
        entry["reference_image"] = base.get("reference_image") or entry.get("reference_image") or ""
    return entry


def iter_character_sheet_targets(name: str, entry: dict[str, Any]) -> list[tuple[str, str]]:
    """该角色每个已登记造型的 ``(resource_id, sheet相对路径)``，base 在前。"""

    targets: list[tuple[str, str]] = []
    for look_id in list_look_ids(entry):
        look = None if look_id == BASE_LOOK_ID else look_id
        targets.append((look_asset_id(name, look), resolve_look_sheet_path(entry, look)))
    return targets


def list_look_ids(entry: dict[str, Any]) -> list[str]:
    """已登记 look id，``base`` 始终第一，其余按名称排序。"""

    ensure_character_looks(entry)
    looks = entry.get("looks")
    if not isinstance(looks, dict):
        return [BASE_LOOK_ID]
    others = sorted(
        key
        for key in looks
        if isinstance(key, str) and asset_name_comparison_key(key) != BASE_LOOK_ID and isinstance(looks.get(key), dict)
    )
    return [BASE_LOOK_ID, *others]


def get_look_entry(character_entry: dict[str, Any], look: str | None) -> dict[str, Any] | None:
    """取指定 look 的子 entry；未知 look 返回 ``None``。基准始终可解析。"""

    ensure_character_looks(character_entry)
    looks = character_entry.get("looks")
    if not isinstance(looks, dict):
        return None
    effective = normalize_look_id(look)
    look_key = BASE_LOOK_ID if effective is None else effective
    # 表内 key 可能未归一；按比对键查找
    for key, value in looks.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        if asset_name_comparison_key(key) == look_key:
            return value
    return None


def look_is_registered(character_entry: dict[str, Any], look: str | None) -> bool:
    """基准恒为已登记；非基准须在 ``looks`` 中有对象 entry。"""

    if normalize_look_id(look) is None:
        return True
    return get_look_entry(character_entry, look) is not None


def resolve_look_sheet_path(character_entry: dict[str, Any], look: str | None) -> str:
    """该造型登记的 sheet 相对路径；无则空串。"""

    look_entry = get_look_entry(character_entry, look)
    if look_entry is None:
        return ""
    sheet = look_entry.get("character_sheet")
    if isinstance(sheet, str) and sheet:
        return sheet
    if normalize_look_id(look) is None:
        top = character_entry.get("character_sheet")
        return top if isinstance(top, str) else ""
    return ""


def resolve_look_original_paths(character_entry: dict[str, Any], look: str | None) -> list[str]:
    """该造型可用的原图路径（目前仅基准 look 继承顶层 ``reference_image``）。"""

    look_entry = get_look_entry(character_entry, look)
    paths: list[str] = []
    if look_entry is not None:
        ref = look_entry.get("reference_image")
        if isinstance(ref, str) and ref:
            paths.append(ref)
    if normalize_look_id(look) is None and not paths:
        top = character_entry.get("reference_image")
        if isinstance(top, str) and top:
            paths.append(top)
    return paths


def sync_base_to_top_level(entry: dict[str, Any]) -> None:
    """把 ``looks.base`` 写回顶层兼容字段。"""

    ensure_character_looks(entry)
    base = entry["looks"][BASE_LOOK_ID]
    if isinstance(base.get("description"), str):
        entry["description"] = base["description"]
    if isinstance(base.get("character_sheet"), str):
        entry["character_sheet"] = base["character_sheet"]
    if isinstance(base.get("reference_image"), str):
        entry["reference_image"] = base["reference_image"]


def set_look_sheet(entry: dict[str, Any], look: str | None, sheet_path: str) -> None:
    """写入造型 sheet，基准时同步顶层 ``character_sheet``。"""

    ensure_character_looks(entry)
    effective = normalize_look_id(look)
    look_key = BASE_LOOK_ID if effective is None else validate_look_id(effective)
    looks = entry["looks"]
    look_entry = looks.get(look_key)
    if not isinstance(look_entry, dict):
        look_entry = {"description": "", "character_sheet": "", "reference_image": ""}
        looks[look_key] = look_entry
    look_entry["character_sheet"] = sheet_path
    if effective is None:
        entry["character_sheet"] = sheet_path


def upsert_look(entry: dict[str, Any], look_id: str, *, description: str = "") -> str:
    """新增或更新非 base look 的 description；返回规范化 look id。不可用于 base。"""

    cleaned = validate_look_id(look_id)
    if cleaned == BASE_LOOK_ID:
        raise ValueError("不能用 upsert_look 覆盖基准造型，请改角色 description")
    ensure_character_looks(entry)
    looks = entry["looks"]
    existing = None
    for key, value in looks.items():
        if isinstance(key, str) and asset_name_comparison_key(key) == cleaned and isinstance(value, dict):
            existing = value
            break
    if existing is None:
        looks[cleaned] = {
            "description": description,
            "character_sheet": "",
            "reference_image": "",
        }
    else:
        if description:
            existing["description"] = description
    return cleaned


def delete_look(entry: dict[str, Any], look_id: str) -> None:
    """删除非 base look；尝试删 base 则报错。"""

    cleaned = validate_look_id(look_id)
    if cleaned == BASE_LOOK_ID:
        raise ValueError("不能删除基准造型")
    ensure_character_looks(entry)
    looks = entry["looks"]
    to_del = [key for key in looks if isinstance(key, str) and asset_name_comparison_key(key) == cleaned]
    if not to_del:
        raise KeyError(cleaned)
    for key in to_del:
        del looks[key]


def renamed_look_stem(stem: str, old_name: str, new_name: str) -> str | None:
    """``旧名`` 或 ``旧名__look`` 的 stem 改写；无关则 ``None``。"""

    normalized = normalize_asset_name(stem)
    target = normalize_asset_name(old_name)
    if normalized == target:
        return new_name
    prefix = f"{target}{LOOK_FILE_SEP}"
    if normalized.startswith(prefix):
        look_part = normalized[len(prefix) :]
        if look_part and LOOK_FILE_SEP not in look_part:
            return f"{new_name}{LOOK_FILE_SEP}{look_part}"
    return None


__all__ = [
    "BASE_LOOK_ID",
    "LOOK_FILE_SEP",
    "LOOK_MENTION_SEP",
    "delete_look",
    "ensure_character_looks",
    "format_mention",
    "get_look_entry",
    "iter_character_sheet_targets",
    "list_look_ids",
    "look_asset_id",
    "look_is_registered",
    "look_sheet_relpath",
    "normalize_look_id",
    "parse_look_asset_id",
    "renamed_look_stem",
    "resolve_look_original_paths",
    "resolve_look_sheet_path",
    "set_look_sheet",
    "split_mention_inner",
    "sync_base_to_top_level",
    "upsert_look",
    "validate_look_id",
]
