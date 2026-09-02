"""参考图超限裁剪：未超限保持提及顺序；超限按类型有用性挑选后再按提及顺序输出。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

# 超限裁剪时的类型优先级：角色身份一致性 > 场景 > 道具 > 商品。
# 正文提及顺序仍是「未超限时」的顺序真相（ADR 0064）；仅当模型参考图上限装不下时，
# 按此优先级挑最有用的若干张，再按原提及顺序输出（保证 图片N 与发出列表同序）。
_REFERENCE_CLAMP_TYPE_PRIORITY: dict[str, int] = {
    "character": 0,
    "scene": 1,
    "prop": 2,
    "product": 3,
}

_T = TypeVar("_T")


def clamp_by_usefulness(
    items: Sequence[_T],
    max_references: int | None,
    *,
    type_of: Callable[[_T], str],
) -> tuple[_T, ...]:
    """超限时按类型有用性挑选，输出仍保持原序列中的相对顺序。"""

    if max_references is None or len(items) <= max_references:
        return tuple(items)
    limit = max(0, max_references)
    ranked = sorted(
        range(len(items)),
        key=lambda index: (_REFERENCE_CLAMP_TYPE_PRIORITY.get(type_of(items[index]), 99), index),
    )
    keep = set(ranked[:limit])
    return tuple(item for index, item in enumerate(items) if index in keep)
