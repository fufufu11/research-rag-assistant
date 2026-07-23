"""``get_embedding_config`` 依赖注入测试（阶段 8.4）。

测试覆盖：
- 从环境变量读 EMBEDDING_MODEL
- 环境变量未设置/空/纯空白时回退到 DEFAULT_EMBEDDING_MODEL（bge-small-zh-v1.5）
- 返回 EmbeddingConfig 实例
"""

from __future__ import annotations

import pytest

from research_rag.api.dependencies import get_embedding_config
from research_rag.embedding import DEFAULT_EMBEDDING_MODEL, EmbeddingConfig

# 相关 Embedding 环境变量清单（测试间清理用，避免本机 .env 污染）
_EMBEDDING_ENV_KEYS = ("EMBEDDING_MODEL",)


@pytest.fixture
def clean_embedding_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清除所有 Embedding 相关环境变量，确保测试隔离。"""
    for key in _EMBEDDING_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_get_embedding_config_reads_env(clean_embedding_env: None) -> None:
    """应从环境变量读 EMBEDDING_MODEL。"""
    import os

    os.environ["EMBEDDING_MODEL"] = "BAAI/bge-m3"

    config = get_embedding_config()

    assert config.model_name == "BAAI/bge-m3"


def test_get_embedding_config_defaults_when_env_unset(clean_embedding_env: None) -> None:
    """环境变量未设置时应回退到 DEFAULT_EMBEDDING_MODEL（bge-small-zh-v1.5 中文优化）。"""
    config = get_embedding_config()

    assert config.model_name == DEFAULT_EMBEDDING_MODEL
    assert config.model_name == "BAAI/bge-small-zh-v1.5"


def test_get_embedding_config_empty_string_falls_back(clean_embedding_env: None) -> None:
    """EMBEDDING_MODEL 为空字符串时应回退到默认值。"""
    import os

    os.environ["EMBEDDING_MODEL"] = ""

    config = get_embedding_config()

    assert config.model_name == DEFAULT_EMBEDDING_MODEL


def test_get_embedding_config_whitespace_only_falls_back(clean_embedding_env: None) -> None:
    """EMBEDDING_MODEL 为纯空白时应回退到默认值。"""
    import os

    os.environ["EMBEDDING_MODEL"] = "   "

    config = get_embedding_config()

    assert config.model_name == DEFAULT_EMBEDDING_MODEL


def test_get_embedding_config_strips_whitespace(clean_embedding_env: None) -> None:
    """EMBEDDING_MODEL 前后空白应被去除。"""
    import os

    os.environ["EMBEDDING_MODEL"] = "  BAAI/bge-small-en-v1.5  "

    config = get_embedding_config()

    assert config.model_name == "BAAI/bge-small-en-v1.5"


def test_get_embedding_config_returns_embedding_config_instance(
    clean_embedding_env: None,
) -> None:
    """get_embedding_config 应返回 EmbeddingConfig 实例（类型检查）。"""
    config = get_embedding_config()
    assert isinstance(config, EmbeddingConfig)
