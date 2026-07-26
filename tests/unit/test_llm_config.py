"""``get_llm_config`` 依赖注入测试。

测试覆盖：
- 从环境变量读 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
- LLM_TIMEOUT / LLM_MAX_RETRIES 正常解析，格式错误回退默认值
- ``LLM_API_KEY_FILE`` 优先于 ``LLM_API_KEY`` 环境变量（docker secrets 支持）
- 返回 LlmConfig 实例
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from research_rag.api.dependencies import get_llm_config
from research_rag.qa_service import (
    DEFAULT_LLM_MAX_RETRIES,
    DEFAULT_LLM_TIMEOUT,
    LlmConfig,
)

if TYPE_CHECKING:
    from pathlib import Path

# 相关 LLM 环境变量清单（测试间清理用，避免本机 .env 污染）
# 含 ``_FILE`` 后缀变量（阶段 11.6 切片 C：docker secrets 支持）
_LLM_ENV_KEYS = (
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_API_KEY_FILE",
    "LLM_MODEL",
    "LLM_TIMEOUT",
    "LLM_MAX_RETRIES",
)


@pytest.fixture
def clean_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清除所有 LLM 相关环境变量，确保测试隔离。"""
    for key in _LLM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_get_llm_config_reads_env(clean_llm_env: None) -> None:
    """应从环境变量读 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL。"""
    import os

    os.environ["LLM_BASE_URL"] = "https://api.deepseek.com"
    os.environ["LLM_API_KEY"] = "sk-test"
    os.environ["LLM_MODEL"] = "deepseek-chat"

    config = get_llm_config()

    assert config.base_url == "https://api.deepseek.com"
    assert config.api_key == "sk-test"
    assert config.model == "deepseek-chat"
    assert config.timeout == DEFAULT_LLM_TIMEOUT
    assert config.max_retries == DEFAULT_LLM_MAX_RETRIES


def test_get_llm_config_defaults_when_env_unset(clean_llm_env: None) -> None:
    """环境变量未设置时应返回空字符串和默认值。"""
    config = get_llm_config()

    assert config.base_url == ""
    assert config.api_key == ""
    assert config.model == ""
    assert config.timeout == DEFAULT_LLM_TIMEOUT
    assert config.max_retries == DEFAULT_LLM_MAX_RETRIES


def test_get_llm_config_parses_timeout_and_retries(clean_llm_env: None) -> None:
    """LLM_TIMEOUT / LLM_MAX_RETRIES 应正确解析为 float / int。"""
    import os

    os.environ["LLM_TIMEOUT"] = "60"
    os.environ["LLM_MAX_RETRIES"] = "5"

    config = get_llm_config()
    assert config.timeout == 60.0
    assert config.max_retries == 5


def test_get_llm_config_invalid_timeout_falls_back(clean_llm_env: None) -> None:
    """LLM_TIMEOUT 格式错误时回退默认值。"""
    import os

    os.environ["LLM_TIMEOUT"] = "not-a-number"

    config = get_llm_config()
    assert config.timeout == DEFAULT_LLM_TIMEOUT


def test_get_llm_config_invalid_retries_falls_back(clean_llm_env: None) -> None:
    """LLM_MAX_RETRIES 格式错误时回退默认值。"""
    import os

    os.environ["LLM_MAX_RETRIES"] = "not-a-number"

    config = get_llm_config()
    assert config.max_retries == DEFAULT_LLM_MAX_RETRIES


def test_get_llm_config_returns_llm_config_instance(clean_llm_env: None) -> None:
    """get_llm_config 应返回 LlmConfig 实例（类型检查）。"""
    config = get_llm_config()
    assert isinstance(config, LlmConfig)


def test_get_llm_config_reads_api_key_from_file(
    clean_llm_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``LLM_API_KEY_FILE`` 指向文件时优先读文件内容（strip 尾部换行）。

    阶段 11.6 切片 C：docker secrets 通过 ``_FILE`` 后缀挂载密钥文件，
    即使 ``LLM_API_KEY`` 环境变量也设置了，也用文件内容。
    """

    key_file = tmp_path / "llm_key.txt"
    key_file.write_text("sk-from-file\n", encoding="utf-8")

    monkeypatch.setenv("LLM_API_KEY_FILE", str(key_file))
    # 即使环境变量也设置了，_FILE 优先
    monkeypatch.setenv("LLM_API_KEY", "sk-env-should-be-ignored")

    config = get_llm_config()
    assert config.api_key == "sk-from-file"


def test_get_llm_config_falls_back_to_env_when_file_missing(
    clean_llm_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``LLM_API_KEY_FILE`` 指向不存在文件时 fallback ``LLM_API_KEY`` 环境变量。"""

    nonexistent = tmp_path / "does-not-exist.txt"
    monkeypatch.setenv("LLM_API_KEY_FILE", str(nonexistent))
    monkeypatch.setenv("LLM_API_KEY", "sk-env-fallback")

    config = get_llm_config()
    assert config.api_key == "sk-env-fallback"
