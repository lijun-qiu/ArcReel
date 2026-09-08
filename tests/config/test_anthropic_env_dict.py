"""build_anthropic_env_dict 行为测试 — 只读 DB、返回 dict、不写 environ。"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

from lib.config.service import build_anthropic_env_dict

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_active_credential_returns_full_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    repo_mock = AsyncMock()
    cred = type(
        "Cred",
        (),
        dict(
            api_key="sk-test",
            api_keys=None,
            base_url="https://api.anthropic.com",
            model="claude-opus-4-7",
            haiku_model="claude-haiku-4-5",
            sonnet_model="claude-sonnet-4-6",
            opus_model="claude-opus-4-7",
            subagent_model="claude-haiku-4-5",
        ),
    )()
    repo_mock.get_active = AsyncMock(return_value=cred)

    setting_repo = AsyncMock()
    setting_repo.get_all = AsyncMock(return_value={})

    monkeypatch.setattr(
        "lib.db.repositories.agent_credential_repo.AgentCredentialRepository",
        lambda _s: repo_mock,
    )
    monkeypatch.setattr("lib.config.service.SystemSettingRepository", lambda _s: setting_repo)

    result = await build_anthropic_env_dict(session)
    assert result["ANTHROPIC_API_KEY"] == "sk-test"
    assert result["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"
    assert result["ANTHROPIC_MODEL"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_no_active_credential_returns_empty_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    repo_mock = AsyncMock()
    repo_mock.get_active = AsyncMock(return_value=None)

    setting_repo = AsyncMock()
    setting_repo.get_all = AsyncMock(return_value={})

    monkeypatch.setattr(
        "lib.db.repositories.agent_credential_repo.AgentCredentialRepository",
        lambda _s: repo_mock,
    )
    monkeypatch.setattr("lib.config.service.SystemSettingRepository", lambda _s: setting_repo)

    result = await build_anthropic_env_dict(session)
    assert result["ANTHROPIC_API_KEY"] == ""
    assert result["ANTHROPIC_BASE_URL"] == ""


@pytest.mark.asyncio
async def test_no_active_credential_falls_back_to_system_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """双轨期兼容：无 active credential 时从 system_settings legacy key 读取。"""
    session = AsyncMock()
    repo_mock = AsyncMock()
    repo_mock.get_active = AsyncMock(return_value=None)

    legacy = {
        "anthropic_api_key": "legacy-sk",
        "anthropic_base_url": "https://legacy.anthropic.com",
        "anthropic_model": "claude-legacy-model",
    }
    setting_repo = AsyncMock()
    setting_repo.get_all = AsyncMock(return_value=legacy)

    monkeypatch.setattr(
        "lib.db.repositories.agent_credential_repo.AgentCredentialRepository",
        lambda _s: repo_mock,
    )
    monkeypatch.setattr("lib.config.service.SystemSettingRepository", lambda _s: setting_repo)

    result = await build_anthropic_env_dict(session)
    assert result["ANTHROPIC_API_KEY"] == "legacy-sk"
    assert result["ANTHROPIC_BASE_URL"] == "https://legacy.anthropic.com"
    assert result["ANTHROPIC_MODEL"] == "claude-legacy-model"
    # 未在 settings 中的 key 仍返回空串
    assert result["CLAUDE_CODE_SUBAGENT_MODEL"] == ""


@pytest.mark.asyncio
async def test_function_does_not_touch_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """spec §6.3 红线：build 函数不能写 os.environ。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    baseline = dict(os.environ)

    session = AsyncMock()
    repo_mock = AsyncMock()
    cred = type(
        "Cred",
        (),
        dict(
            api_key="sk-test",
            api_keys=None,
            base_url="x",
            model="y",
            haiku_model=None,
            sonnet_model=None,
            opus_model=None,
            subagent_model=None,
        ),
    )()
    repo_mock.get_active = AsyncMock(return_value=cred)

    setting_repo = AsyncMock()
    setting_repo.get_all = AsyncMock(return_value={})

    monkeypatch.setattr(
        "lib.db.repositories.agent_credential_repo.AgentCredentialRepository",
        lambda _s: repo_mock,
    )
    monkeypatch.setattr("lib.config.service.SystemSettingRepository", lambda _s: setting_repo)

    await build_anthropic_env_dict(session)
    assert dict(os.environ) == baseline, "build 函数禁止改 os.environ"


@pytest.mark.asyncio
async def test_multi_key_routes_to_embedded_rotator(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    repo_mock = AsyncMock()
    cred = type(
        "Cred",
        (),
        dict(
            api_key="sk-a",
            api_keys='[{"key":"sk-a"},{"key":"sk-b"}]',
            base_url="https://apihub.agnes-ai.com",
            model="agnes-2.5-flash",
            haiku_model=None,
            sonnet_model=None,
            opus_model=None,
            subagent_model=None,
        ),
    )()
    repo_mock.get_active = AsyncMock(return_value=cred)

    setting_repo = AsyncMock()
    setting_repo.get_all = AsyncMock(return_value={})

    class _FakeRotator:
        proxy_token = "proxy-token"
        base_url = "http://127.0.0.1:9"

    monkeypatch.setattr(
        "lib.db.repositories.agent_credential_repo.AgentCredentialRepository",
        lambda _s: repo_mock,
    )
    monkeypatch.setattr("lib.config.service.SystemSettingRepository", lambda _s: setting_repo)
    monkeypatch.setattr(
        "server.agent_runtime.anthropic_key_rotator.get_rotator",
        lambda: _FakeRotator(),
    )

    result = await build_anthropic_env_dict(session)
    assert result["ANTHROPIC_API_KEY"] == "proxy-token"
    assert result["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9"
