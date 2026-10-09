"""SDK MCP tool：提交本集资产完备审查（造型 + 场景/道具）完成事实。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from claude_agent_sdk import tool

from lib.episode_wardrobe import (
    EpisodeWardrobeError,
    EpisodeWardrobeInvalidRequest,
    EpisodeWardrobeRevisionConflict,
    complete_episode_wardrobe,
)
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error


def _json_response(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    response: dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]
    }
    if is_error:
        response["is_error"] = True
    return response


def complete_episode_wardrobe_tool(ctx: ToolContext):
    @tool(
        "complete_episode_wardrobe",
        "参考生视频在拆本集 unit 之前提交资产完备审查：按需新增跨镜造型、本集场景与道具"
        "（已有条目跳过，写入 project.json 共用资产表），并写入本集完成事实。"
        "空 looks/scenes/props 表示对应类别无需新增，仍须调用。"
        "工具在项目锁内重算 source/episode_N.txt 指纹，与 expected_episode_source_revision 不一致时整笔拒绝。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "minimum": 1},
                "expected_episode_source_revision": {"type": "string"},
                "looks": {
                    "type": "object",
                    "description": "本次新增造型：{ 角色名: { 造型名: 视觉描述 } }；不要含 base",
                },
                "scenes": {
                    "type": "object",
                    "description": "本次新登记场景：{ 场景名: 视觉描述 }；已有名称跳过",
                },
                "props": {
                    "type": "object",
                    "description": "本次新登记道具：{ 道具名: 视觉描述 }；已有名称跳过",
                },
            },
            "required": ["episode", "expected_episode_source_revision"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            completed = await asyncio.to_thread(
                complete_episode_wardrobe,
                ctx.pm,
                ctx.project_name,
                args.get("episode"),
                args.get("expected_episode_source_revision"),
                args.get("looks"),
                args.get("scenes"),
                args.get("props"),
            )
            return _json_response(
                {
                    "episode": completed.episode,
                    "source_revision": completed.source_revision,
                    "added": list(completed.added),
                    "added_scenes": list(completed.added_scenes),
                    "added_props": list(completed.added_props),
                }
            )
        except EpisodeWardrobeRevisionConflict as exc:
            return _json_response(
                {
                    "error": "episode_source_revision_conflict",
                    "expected_episode_source_revision": exc.expected_revision,
                    "actual_episode_source_revision": exc.actual_revision,
                },
                is_error=True,
            )
        except EpisodeWardrobeInvalidRequest as exc:
            return _json_response({"error": "invalid_request", "detail": str(exc)}, is_error=True)
        except EpisodeWardrobeError as exc:
            return _json_response({"error": "wardrobe_unavailable", "detail": str(exc)}, is_error=True)
        except Exception as exc:  # noqa: BLE001
            return tool_error("complete_episode_wardrobe", exc)

    return _handler


__all__ = ["complete_episode_wardrobe_tool"]
