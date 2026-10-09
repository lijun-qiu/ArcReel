"""本地开发默认：种子写入 OpenRouter / ModelScope / Agnes 凭证与默认后端。

对齐 huobao-drama 的免费文本代理与 Agnes 图像/视频配置。幂等：已有活跃凭证或已写过
对应 system setting 时不覆盖用户改动。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.registry import (
    MODELSCOPE_LOCAL_PROXY_BASE_URL,
    OPENROUTER_LOCAL_PROXY_BASE_URL,
)
from lib.config.repository import SystemSettingRepository
from lib.config.service import (
    _DEFAULT_TEXT_BACKEND,
    _DEFAULT_TEXT_BACKEND_COMPLEX,
    _DEFAULT_TEXT_BACKEND_SIMPLE,
)
from lib.db.repositories.credential_repository import CredentialRepository

logger = logging.getLogger(__name__)

# 与 huobao-drama 本机注入代理约定一致（非上游真实 sk-or / ms-*）
_OPENROUTER_PROXY_KEY = "openrouter_proxy_injector_api_key"
_OPENROUTER_PROXY_BASE = OPENROUTER_LOCAL_PROXY_BASE_URL
_MODELSCOPE_PROXY_KEY = "modelscope_proxy_api_key"
_MODELSCOPE_PROXY_BASE = MODELSCOPE_LOCAL_PROXY_BASE_URL

# 本地开发从 huobao-drama 同步的 Agnes Key（写入 DB，不进进程 env）
_AGNES_API_KEY = "sk-KUTMgK1nOgrFgLjEvMIq3lkWohXtlJLh1XEgAb8YaRZlCAxo"
_AGNES_BASE_URL = "https://apihub.agnes-ai.com/v1"

_DEFAULT_IMAGE = "agnes/agnes-image-2.5-flash"
_DEFAULT_VIDEO = "agnes/agnes-video-v2.0"

_SEED_MARKER = "local_provider_defaults_seeded"


async def seed_local_provider_defaults(session: AsyncSession) -> None:
    """首次启动时写入免费文本代理 + Agnes 媒体凭证，并设置全局默认后端。

    文本档位（简单 Flash / 复杂 Pro）每次启动都补空值，不覆盖用户已配。
    """
    settings = SystemSettingRepository(session)
    await _ensure_modelscope_text_tiers(settings)

    if await settings.get(_SEED_MARKER):
        await session.commit()
        return

    creds = CredentialRepository(session)

    await _ensure_credential(
        creds,
        provider="openrouter",
        name="本机 OpenRouter 免费代理",
        api_key=_OPENROUTER_PROXY_KEY,
        base_url=_OPENROUTER_PROXY_BASE,
    )
    await _ensure_credential(
        creds,
        provider="modelscope",
        name="本机魔搭免费代理",
        api_key=_MODELSCOPE_PROXY_KEY,
        base_url=_MODELSCOPE_PROXY_BASE,
    )
    await _ensure_credential(
        creds,
        provider="agnes",
        name="Agnes（图像/视频）",
        api_key=_AGNES_API_KEY,
        base_url=_AGNES_BASE_URL,
    )

    await _ensure_setting_if_empty(settings, "default_text_backend", _DEFAULT_TEXT_BACKEND)
    await _ensure_setting_if_empty(settings, "default_image_backend", _DEFAULT_IMAGE)
    await _ensure_setting_if_empty(settings, "default_video_backend", _DEFAULT_VIDEO)

    await settings.set(_SEED_MARKER, "1")
    await session.commit()
    logger.info(
        "已种子本地默认供应商：文本简单=%s 复杂=%s 默认=%s 图像=%s 视频=%s",
        _DEFAULT_TEXT_BACKEND_SIMPLE,
        _DEFAULT_TEXT_BACKEND_COMPLEX,
        _DEFAULT_TEXT_BACKEND,
        _DEFAULT_IMAGE,
        _DEFAULT_VIDEO,
    )


async def _ensure_modelscope_text_tiers(settings: SystemSettingRepository) -> None:
    """空档位补魔搭：简单 Flash、复杂 Pro；不覆盖已有配置。"""
    await _ensure_setting_if_empty(settings, "text_backend_simple", _DEFAULT_TEXT_BACKEND_SIMPLE)
    await _ensure_setting_if_empty(settings, "text_backend_complex", _DEFAULT_TEXT_BACKEND_COMPLEX)
    await _ensure_setting_if_empty(settings, "default_text_backend", _DEFAULT_TEXT_BACKEND)


async def _ensure_credential(
    creds: CredentialRepository,
    *,
    provider: str,
    name: str,
    api_key: str,
    base_url: str,
) -> None:
    if await creds.get_active(provider) is not None:
        return
    existing = await creds.list_by_provider(provider)
    if existing:
        return
    await creds.create(provider=provider, name=name, api_key=api_key, base_url=base_url)


async def _ensure_setting_if_empty(settings: SystemSettingRepository, key: str, value: str) -> None:
    current = (await settings.get(key, "")).strip()
    if current:
        return
    await settings.set(key, value)
