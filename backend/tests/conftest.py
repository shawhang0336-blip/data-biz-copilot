"""Pytest 全局确定性隔离：单元测试默认使用 scripted 模式，避免依赖本地 .env 触发真实外网模型调用。"""

import pytest

from app.infra.config import get_settings


@pytest.fixture(autouse=True)
def _force_scripted_llm_for_unit_tests(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "scripted")
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
