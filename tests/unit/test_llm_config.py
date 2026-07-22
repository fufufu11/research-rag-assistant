"""``get_llm_config`` 依赖注入的 provider 分发测试。

测试覆盖：
- 默认 provider=openai，读 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
- LLM_PROVIDER=ollama，读 OLLAMA_BASE_URL / OLLAMA_MODEL，不读 API Key
- LLM_PROVIDER 大小写不敏感（"Ollama" / "OLLAMA" 等价）
- OLLAMA_BASE_URL 未设置时用 DEFAULT_OLLAMA_BASE_URL
- LLM_TIMEOUT / LLM_MAX_RETRIES 两个 provider 共享，格式错误回退默认值
"""

from __future__ import annotations

import pytest

from research_rag.api.dependencies import get_llm_config
from research_rag.qa_service import (
    DEFAULT_LLM_MAX_RETRIES,
    DEFAULT_LLM_TIMEOUT,
    DEFAULT_OLLAMA_BASE_URL,
    LlmConfig,
)

# 相关 LLM 环境变量清单（测试间清理用，避免本机 .env 污染）
_LLM_ENV_KEYS = (
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "LLM_TIMEOUT",
    "LLM_MAX_RETRIES",
)


@pytest.fixture
def clean_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清除所有 LLM 相关环境变量，确保测试隔离。"""
    for key in _LLM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_get_llm_config_default_openai(clean_llm_env: None) -> None:
    """不设 LLM_PROVIDER 时默认 openai，读 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL。"""
    import os

    os.environ["LLM_BASE_URL"] = "https://api.deepseek.com"
    os.environ["LLM_API_KEY"] = "sk-test"
    os.environ["LLM_MODEL"] = "deepseek-chat"

    config = get_llm_config()

    assert config.provider == "openai"
    assert config.base_url == "https://api.deepseek.com"
    assert config.api_key == "sk-test"
    assert config.model == "deepseek-chat"
    assert config.timeout == DEFAULT_LLM_TIMEOUT
    assert config.max_retries == DEFAULT_LLM_MAX_RETRIES


def test_get_llm_config_ollama_provider(clean_llm_env: None) -> None:
    """LLM_PROVIDER=ollama 时读 OLLAMA_BASE_URL / OLLAMA_MODEL，不需要 API Key。"""
    import os

    os.environ["LLM_PROVIDER"] = "ollama"
    os.environ["OLLAMA_BASE_URL"] = "http://192.168.1.100:11434"
    os.environ["OLLAMA_MODEL"] = "qwen2.5:3b-instruct"

    config = get_llm_config()

    assert config.provider == "ollama"
    assert config.base_url == "http://192.168.1.100:11434"
    assert config.model == "qwen2.5:3b-instruct"
    # ollama provider 不读 API Key
    assert config.api_key == ""
    assert config.timeout == DEFAULT_LLM_TIMEOUT
    assert config.max_retries == DEFAULT_LLM_MAX_RETRIES


def test_get_llm_config_ollama_default_base_url(clean_llm_env: None) -> None:
    """LLM_PROVIDER=ollama 且 OLLAMA_BASE_URL 未设置时用 DEFAULT_OLLAMA_BASE_URL。"""
    import os

    os.environ["LLM_PROVIDER"] = "ollama"
    os.environ["OLLAMA_MODEL"] = "qwen2.5:3b-instruct"

    config = get_llm_config()

    assert config.provider == "ollama"
    assert config.base_url == DEFAULT_OLLAMA_BASE_URL


@pytest.mark.parametrize("value", ["Ollama", "OLLAMA", "Ollama", "  ollama  "])
def test_get_llm_config_provider_case_insensitive(clean_llm_env: None, value: str) -> None:
    """LLM_PROVIDER 大小写和首尾空格不敏感。"""
    import os

    os.environ["LLM_PROVIDER"] = value
    os.environ["OLLAMA_MODEL"] = "qwen2.5:3b-instruct"

    config = get_llm_config()
    assert config.provider == "ollama"


def test_get_llm_config_shared_timeout_and_retries(clean_llm_env: None) -> None:
    """LLM_TIMEOUT / LLM_MAX_RETRIES 两个 provider 共享。"""
    import os

    os.environ["LLM_PROVIDER"] = "ollama"
    os.environ["OLLAMA_MODEL"] = "qwen2.5:3b-instruct"
    os.environ["LLM_TIMEOUT"] = "60"
    os.environ["LLM_MAX_RETRIES"] = "5"

    config = get_llm_config()
    assert config.timeout == 60.0
    assert config.max_retries == 5


def test_get_llm_config_invalid_timeout_falls_back(clean_llm_env: None) -> None:
    """LLM_TIMEOUT 格式错误时回退默认值。"""
    import os

    os.environ["LLM_PROVIDER"] = "ollama"
    os.environ["OLLAMA_MODEL"] = "qwen2.5:3b-instruct"
    os.environ["LLM_TIMEOUT"] = "not-a-number"

    config = get_llm_config()
    assert config.timeout == DEFAULT_LLM_TIMEOUT


def test_get_llm_config_returns_llm_config_instance(clean_llm_env: None) -> None:
    """get_llm_config 应返回 LlmConfig 实例（类型检查）。"""
    config = get_llm_config()
    assert isinstance(config, LlmConfig)
