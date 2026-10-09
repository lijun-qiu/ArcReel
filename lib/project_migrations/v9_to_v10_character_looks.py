"""v9→v10：角色 entry 补齐衣橱 ``looks.base``。

存量角色只有顶层 ``description`` / ``character_sheet`` / ``reference_image``。本迁移为每个
角色写入 ``looks.base`` 并与顶层字段对齐（见 ADR 0066），不改剧本正文、不新建非 base look。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.asset_types import BUCKET_KEY
from lib.character_looks import ensure_character_looks
from lib.json_io import atomic_write_json
from lib.project_migration_failure import ProjectMigrationError

_TARGET_VERSION = 10


def migrate_v9_to_v10(project_dir: Path) -> None:
    """为项目内全部角色补 ``looks.base``，提交 schema_version=10。"""

    project_path = project_dir / "project.json"
    try:
        project: dict[str, Any] = json.loads(project_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ProjectMigrationError(f"无法读取 project.json: {exc}") from exc

    characters = project.get(BUCKET_KEY["character"])
    if characters is None:
        project[BUCKET_KEY["character"]] = {}
        characters = project[BUCKET_KEY["character"]]
    if not isinstance(characters, dict):
        raise ProjectMigrationError("characters 必须是对象")

    for name, entry in list(characters.items()):
        if not isinstance(entry, dict):
            raise ProjectMigrationError(f"characters[{name!r}] 必须是对象")
        ensure_character_looks(entry)

    project["schema_version"] = _TARGET_VERSION
    atomic_write_json(project_path, project)


__all__ = ["migrate_v9_to_v10"]
