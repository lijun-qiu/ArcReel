"""参考生视频：每集 step1 前登记跨镜造型与本集场景/道具的完成事实。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from lib.asset_types import BUCKET_KEY, resolve_asset_key, validate_asset_name
from lib.character_looks import look_is_registered, upsert_look, validate_look_id
from lib.content_digest import PREFIXED_DIGEST_RE, prefixed_sha256_file
from lib.episode_ledger import parse_positive_episode_num
from lib.project_manager import ProjectManager

EPISODE_WARDROBE_KEY = "episode_wardrobe"


class EpisodeWardrobeError(ValueError):
    """衣橱 / 本集资产完备完成失败基类。"""


class EpisodeWardrobeInvalidRequest(EpisodeWardrobeError):
    """完成请求本身不合法。"""


class EpisodeWardrobeRevisionConflict(EpisodeWardrobeError):
    """本集派生源文在提交完成事实前已变。"""

    def __init__(self, expected_revision: str, actual_revision: str) -> None:
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__("episode source changed before wardrobe completion")


@dataclass(frozen=True)
class EpisodeWardrobeCompletion:
    episode: int
    source_revision: str
    added: tuple[str, ...]
    added_scenes: tuple[str, ...]
    added_props: tuple[str, ...]


def episode_source_relpath(episode: int) -> str:
    """账本派生物 ``source/episode_{N}.txt`` 的相对路径。"""

    return f"source/episode_{episode}.txt"


def episode_source_revision(project_path: object, episode: int) -> str | None:
    """本集派生源文的带前缀 sha256；文件不存在则 ``None``。"""

    from pathlib import Path

    path = Path(str(project_path)) / episode_source_relpath(episode)
    try:
        return prefixed_sha256_file(path)
    except FileNotFoundError:
        return None


def wardrobe_marker_matches(project: Mapping[str, Any], episode: int, live_revision: str) -> bool:
    """``workflow.episode_wardrobe`` 中该集记录是否对应当前派生源文。"""

    workflow = project.get("workflow")
    if not isinstance(workflow, Mapping):
        return False
    marker = workflow.get(EPISODE_WARDROBE_KEY)
    if not isinstance(marker, Mapping):
        return False
    entry = marker.get(str(episode))
    if entry is None:
        entry = marker.get(episode)
    if not isinstance(entry, Mapping):
        return False
    return entry.get("source_revision") == live_revision


def complete_episode_wardrobe(
    pm: ProjectManager,
    project_name: str,
    episode: object,
    expected_source_revision: object,
    looks: object = None,
    scenes: object = None,
    props: object = None,
) -> EpisodeWardrobeCompletion:
    """在项目锁内写入本集新增造型 / 场景 / 道具（已有的跳过）并提交完成事实。

    场景与道具登记进 ``project.json`` 与全局 ``analyze_assets`` 同一张表，后续
    ``generate_asset_sheets`` / step1 ``@[名称]`` 校验走共用路径，不另建旁路。
    """

    number = parse_positive_episode_num(episode)
    if number is None:
        raise EpisodeWardrobeInvalidRequest("episode must be a positive integer")
    if not isinstance(expected_source_revision, str) or PREFIXED_DIGEST_RE.fullmatch(expected_source_revision) is None:
        raise EpisodeWardrobeInvalidRequest("expected_episode_source_revision must be a sha256-v1 revision")
    prepared_looks = _prepare_looks(looks)
    prepared_scenes = _prepare_named_descriptions(scenes, field="scenes")
    prepared_props = _prepare_named_descriptions(props, field="props")

    result: EpisodeWardrobeCompletion | None = None
    project_path = pm.get_project_path(project_name)

    def _mutate(project: dict[str, Any]) -> None:
        nonlocal result
        if project.get("generation_mode") != "reference_video":
            raise EpisodeWardrobeInvalidRequest("episode wardrobe applies only to reference_video projects")
        live = episode_source_revision(project_path, number)
        if live is None:
            raise EpisodeWardrobeInvalidRequest(f"missing {episode_source_relpath(number)}")
        if live != expected_source_revision:
            raise EpisodeWardrobeRevisionConflict(expected_source_revision, live)

        added: list[str] = []
        added_scenes: list[str] = []
        added_props: list[str] = []

        bucket = project.get("characters")
        if prepared_looks and not isinstance(bucket, dict):
            raise EpisodeWardrobeInvalidRequest("characters must be an object")
        for char_name, look_map in prepared_looks.items():
            if not isinstance(bucket, dict):
                break
            key = resolve_asset_key(bucket, char_name)
            if key is None:
                raise EpisodeWardrobeInvalidRequest(f"character {char_name!r} is not registered")
            entry = bucket[key]
            if not isinstance(entry, dict):
                raise EpisodeWardrobeInvalidRequest(f"characters[{key!r}] must be an object")
            for look_id, description in look_map.items():
                if look_is_registered(entry, look_id):
                    continue
                upsert_look(entry, look_id, description=description)
                added.append(f"{key}@{look_id}")

        added_scenes.extend(
            _upsert_plain_assets(project, BUCKET_KEY["scene"], prepared_scenes, sheet_field="scene_sheet")
        )
        added_props.extend(
            _upsert_plain_assets(project, BUCKET_KEY["prop"], prepared_props, sheet_field="prop_sheet")
        )

        workflow = project.get("workflow")
        if workflow is None:
            workflow = {}
            project["workflow"] = workflow
        elif not isinstance(workflow, dict):
            raise EpisodeWardrobeError("workflow must be an object")
        table = workflow.get(EPISODE_WARDROBE_KEY)
        if not isinstance(table, dict):
            table = {}
            workflow[EPISODE_WARDROBE_KEY] = table
        table[str(number)] = {
            "source_revision": live,
            "completed_at": datetime.now(UTC).isoformat(),
        }
        result = EpisodeWardrobeCompletion(
            episode=number,
            source_revision=live,
            added=tuple(added),
            added_scenes=tuple(added_scenes),
            added_props=tuple(added_props),
        )

    pm.update_project(project_name, _mutate)
    if result is None:  # pragma: no cover
        raise RuntimeError("episode wardrobe completion did not run")
    return result


def _upsert_plain_assets(
    project: dict[str, Any],
    bucket_key: str,
    entries: Mapping[str, str],
    *,
    sheet_field: str,
) -> list[str]:
    """向 scenes / props 表登记尚不存在的名称；已有名称跳过（不覆盖人工描述）。"""

    if not entries:
        return []
    bucket = project.get(bucket_key)
    if bucket is None:
        bucket = {}
        project[bucket_key] = bucket
    elif not isinstance(bucket, dict):
        raise EpisodeWardrobeInvalidRequest(f"{bucket_key} must be an object")
    added: list[str] = []
    for name, description in entries.items():
        existing_key = resolve_asset_key(bucket, name)
        if existing_key is not None:
            continue
        bucket[name] = {"description": description, sheet_field: ""}
        added.append(name)
    return added


def _prepare_named_descriptions(raw: object, *, field: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise EpisodeWardrobeInvalidRequest(f"{field} must be an object")
    prepared: dict[str, str] = {}
    for raw_name, raw_desc in raw.items():
        try:
            name = validate_asset_name(raw_name)
        except ValueError as exc:
            raise EpisodeWardrobeInvalidRequest(f"{field}: {exc}") from exc
        if name in prepared:
            raise EpisodeWardrobeInvalidRequest(f"{field} contains duplicate name {name!r}")
        if not isinstance(raw_desc, str) or not raw_desc.strip():
            raise EpisodeWardrobeInvalidRequest(f"{field}[{name!r}] description must be a non-empty string")
        prepared[name] = raw_desc.strip()
    return prepared


def _prepare_looks(looks: object) -> dict[str, dict[str, str]]:
    if looks is None:
        return {}
    if not isinstance(looks, Mapping):
        raise EpisodeWardrobeInvalidRequest("looks must be an object")
    prepared: dict[str, dict[str, str]] = {}
    for raw_name, raw_looks in looks.items():
        try:
            name = validate_asset_name(raw_name)
        except ValueError as exc:
            raise EpisodeWardrobeInvalidRequest(f"looks: {exc}") from exc
        if name in prepared:
            raise EpisodeWardrobeInvalidRequest(f"looks contains duplicate character {name!r}")
        if not isinstance(raw_looks, Mapping):
            raise EpisodeWardrobeInvalidRequest(f"looks[{name!r}] must be an object")
        look_map: dict[str, str] = {}
        for raw_look, raw_desc in raw_looks.items():
            try:
                look_id = validate_look_id(raw_look)
            except ValueError as exc:
                raise EpisodeWardrobeInvalidRequest(f"looks[{name!r}]: {exc}") from exc
            if look_id == "base":
                raise EpisodeWardrobeInvalidRequest(f"looks[{name!r}] cannot upsert base")
            if look_id in look_map:
                raise EpisodeWardrobeInvalidRequest(f"looks[{name!r}] contains duplicate look {look_id!r}")
            if not isinstance(raw_desc, str):
                raise EpisodeWardrobeInvalidRequest(f"looks[{name!r}][{look_id!r}] description must be a string")
            look_map[look_id] = raw_desc
        prepared[name] = look_map
    return prepared


__all__ = [
    "EPISODE_WARDROBE_KEY",
    "EpisodeWardrobeCompletion",
    "EpisodeWardrobeError",
    "EpisodeWardrobeInvalidRequest",
    "EpisodeWardrobeRevisionConflict",
    "complete_episode_wardrobe",
    "episode_source_relpath",
    "episode_source_revision",
    "wardrobe_marker_matches",
]
