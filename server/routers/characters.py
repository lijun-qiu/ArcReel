"""角色管理路由（CRUD 由 _asset_router_factory 统一生成；衣橱 look 为附加子资源）。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from lib.api_errors import NotFoundError, UnprocessableError
from lib.asset_types import resolve_asset_key, validate_asset_name
from lib.character_looks import (
    BASE_LOOK_ID,
    ensure_character_looks,
    list_look_ids,
    look_sheet_relpath,
    validate_look_id,
)
from lib.i18n import Translator
from lib.project_manager import get_project_manager
from server.routers._asset_router_factory import build_asset_router

# late-binding 必需：测试通过 monkeypatch.setattr(characters, "get_project_manager", ...) 替换模块属性
router: APIRouter = build_asset_router(asset_type="character", pm_getter=lambda: get_project_manager())  # noqa: PLW0108


class CharacterLookUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    look_id: str = Field(min_length=1)
    description: str = ""


class CharacterLookPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = None


@router.get("/projects/{project_name}/characters/{char_name}/looks")
async def list_character_looks(project_name: str, char_name: str, _t: Translator):
    """列出角色衣橱造型（含 base）。"""

    del _t
    pm = get_project_manager()
    project = pm.load_project(project_name)
    key = resolve_asset_key(project.get("characters"), char_name)
    if key is None:
        raise NotFoundError("character_not_found", name=char_name)
    entry = project["characters"][key]
    ensure_character_looks(entry)
    looks = entry.get("looks") or {}
    items = []
    for look_id in list_look_ids(entry):
        look_entry = looks.get(look_id) if isinstance(looks, dict) else None
        if not isinstance(look_entry, dict):
            continue
        items.append(
            {
                "look_id": look_id,
                "description": look_entry.get("description") or "",
                "character_sheet": look_entry.get("character_sheet") or "",
                "is_base": look_id == BASE_LOOK_ID,
            }
        )
    return {"character": key, "looks": items}


@router.put("/projects/{project_name}/characters/{char_name}/looks/{look_id}")
async def upsert_character_look(
    project_name: str,
    char_name: str,
    look_id: str,
    req: CharacterLookPatchRequest,
    _t: Translator,
):
    """新增或更新非 base look；body 可只带 description。"""

    del _t
    try:
        cleaned = validate_look_id(look_id)
    except ValueError as exc:
        raise UnprocessableError("invalid_look_id", detail=str(exc)) from exc
    if cleaned == BASE_LOOK_ID:
        raise UnprocessableError("cannot_upsert_base_look")
    pm = get_project_manager()
    try:
        pm.upsert_character_look(
            project_name,
            char_name,
            cleaned,
            description=req.description or "",
        )
    except KeyError as exc:
        raise NotFoundError("character_not_found", name=char_name) from exc
    except ValueError as exc:
        raise UnprocessableError("invalid_look", detail=str(exc)) from exc
    return {"success": True, "look_id": cleaned}


@router.post("/projects/{project_name}/characters/{char_name}/looks")
async def create_character_look(
    project_name: str,
    char_name: str,
    req: CharacterLookUpsertRequest,
    _t: Translator,
):
    """创建衣橱造型（非 base）。"""

    del _t
    try:
        cleaned = validate_look_id(req.look_id)
    except ValueError as exc:
        raise UnprocessableError("invalid_look_id", detail=str(exc)) from exc
    if cleaned == BASE_LOOK_ID:
        raise UnprocessableError("cannot_upsert_base_look")
    pm = get_project_manager()
    try:
        validate_asset_name(char_name)
        pm.upsert_character_look(project_name, char_name, cleaned, description=req.description)
    except KeyError as exc:
        raise NotFoundError("character_not_found", name=char_name) from exc
    except ValueError as exc:
        raise UnprocessableError("invalid_look", detail=str(exc)) from exc
    return {
        "success": True,
        "look_id": cleaned,
        "sheet_path": look_sheet_relpath(char_name, cleaned),
    }


@router.delete("/projects/{project_name}/characters/{char_name}/looks/{look_id}")
async def delete_character_look_route(project_name: str, char_name: str, look_id: str, _t: Translator):
    """删除非 base look。"""

    del _t
    pm = get_project_manager()
    try:
        pm.delete_character_look(project_name, char_name, look_id)
    except KeyError as exc:
        raise NotFoundError("look_not_found", name=look_id) from exc
    except ValueError as exc:
        raise UnprocessableError("cannot_delete_look", detail=str(exc)) from exc
    return {"success": True}


__all__ = ["router"]
