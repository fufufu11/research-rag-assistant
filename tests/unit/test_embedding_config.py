"""``get_embedding_config`` 依赖注入测试（阶段 8.4）。

测试覆盖：
- 从环境变量读 EMBEDDING_MODEL
- 环境变量未设置/空/纯空白时回退到 DEFAULT_EMBEDDING_MODEL（bge-small-zh-v1.5）
- ``DASHSCOPE_API_KEY_FILE`` / ``JINA_API_KEY_FILE`` 优先于环境变量（docker secrets 支持）
- 返回 EmbeddingConfig 实例
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from research_rag.api.dependencies import get_embedding_config
from research_rag.embedding import (
    DASHSCOPE_DEFAULT_MODEL,
    DEFAULT_EMBEDDING_MODEL,
    JINA_DEFAULT_MODEL,
    EmbeddingConfig,
)

if TYPE_CHECKING:
    from pathlib import Path

# 相关 Embedding 环境变量清单（测试间清理用，避免本机 .env 污染）
# 含 ``_FILE`` 后缀变量（阶段 11.6 切片 C：docker secrets 支持）
_EMBEDDING_ENV_KEYS = (
    "EMBEDDING_MODEL",
    "EMBEDDING_PROVIDER",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_API_KEY_FILE",
    "JINA_API_KEY",
    "JINA_API_KEY_FILE",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_DIMENSIONS",
    "EMBEDDING_BATCH_SIZE",
)


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


# ---------------------------------------------------------------------------
# EMBEDDING_PROVIDER 切换（阶段 8.4：接入阿里百炼 API）
# ---------------------------------------------------------------------------


def test_get_embedding_config_default_provider_is_local(clean_embedding_env: None) -> None:
    """未设 EMBEDDING_PROVIDER 时默认 local。"""
    config = get_embedding_config()

    assert config.provider == "local"


def test_get_embedding_config_reads_provider_dashscope(
    clean_embedding_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EMBEDDING_PROVIDER=dashscope 时应切换到 dashscope 模式。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")

    config = get_embedding_config()

    assert config.provider == "dashscope"


def test_get_embedding_config_provider_case_insensitive(
    clean_embedding_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EMBEDDING_PROVIDER 大小写不敏感（DashScope → dashscope）。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "DashScope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")

    config = get_embedding_config()

    assert config.provider == "dashscope"


def test_get_embedding_config_dashscope_defaults_model(
    clean_embedding_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dashscope 模式下 EMBEDDING_MODEL 未设时应默认 text-embedding-v4。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")

    config = get_embedding_config()

    assert config.model_name == DASHSCOPE_DEFAULT_MODEL
    assert config.model_name == "text-embedding-v4"


def test_get_embedding_config_dashscope_reads_model(
    clean_embedding_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dashscope 模式下 EMBEDDING_MODEL 应覆盖默认模型名。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-v3")

    config = get_embedding_config()

    assert config.model_name == "text-embedding-v3"


def test_get_embedding_config_dashscope_reads_api_key(
    clean_embedding_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dashscope 模式应从 DASHSCOPE_API_KEY 读取 API Key。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-from-env")

    config = get_embedding_config()

    assert config.api_key == "sk-from-env"


def test_get_embedding_config_dashscope_reads_base_url(
    clean_embedding_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dashscope 模式应从 EMBEDDING_BASE_URL 读取自定义 endpoint。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://custom.example.com/v1")

    config = get_embedding_config()

    assert config.base_url == "https://custom.example.com/v1"


def test_get_embedding_config_dashscope_dimensions_and_batch(
    clean_embedding_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dashscope 模式应从 EMBEDDING_DIMENSIONS / EMBEDDING_BATCH_SIZE 读取配置。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "768")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "8")

    config = get_embedding_config()

    assert config.dimensions == 768
    assert config.batch_size == 8


def test_get_embedding_config_dashscope_invalid_dimensions_falls_back(
    clean_embedding_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EMBEDDING_DIMENSIONS 格式错误时应回退到 0（用 dashscope 默认维度）。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "not-a-number")

    config = get_embedding_config()

    assert config.dimensions == 0


def test_get_embedding_config_local_ignores_dashscope_vars(
    clean_embedding_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """provider=local 时 dashscope 相关变量应被忽略，走本地 HuggingFace 路径。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-ignored")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "999")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "999")

    config = get_embedding_config()

    assert config.provider == "local"
    assert config.api_key == ""
    assert config.dimensions == 0
    assert config.batch_size == 0
    assert config.model_name == DEFAULT_EMBEDDING_MODEL


# ---------------------------------------------------------------------------
# EMBEDDING_PROVIDER=jina（阶段 8.4：接入 Jina AI API）
# ---------------------------------------------------------------------------


def test_get_embedding_config_reads_provider_jina(
    clean_embedding_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EMBEDDING_PROVIDER=jina 时应切换到 jina 模式。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "jina")
    monkeypatch.setenv("JINA_API_KEY", "jina-test")

    config = get_embedding_config()

    assert config.provider == "jina"


def test_get_embedding_config_jina_defaults_model(
    clean_embedding_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """jina 模式下 EMBEDDING_MODEL 未设时应默认 jina-embeddings-v3。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "jina")
    monkeypatch.setenv("JINA_API_KEY", "jina-test")

    config = get_embedding_config()

    assert config.model_name == JINA_DEFAULT_MODEL
    assert config.model_name == "jina-embeddings-v3"


def test_get_embedding_config_jina_reads_api_key(
    clean_embedding_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """jina 模式应从 JINA_API_KEY 读取 API Key。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "jina")
    monkeypatch.setenv("JINA_API_KEY", "jina-from-env")

    config = get_embedding_config()

    assert config.api_key == "jina-from-env"


def test_get_embedding_config_jina_reads_base_url(
    clean_embedding_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """jina 模式应从 EMBEDDING_BASE_URL 读取自定义 endpoint。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "jina")
    monkeypatch.setenv("JINA_API_KEY", "jina-test")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://custom.jina.example/v1")

    config = get_embedding_config()

    assert config.base_url == "https://custom.jina.example/v1"


def test_get_embedding_config_jina_reads_dimensions_and_batch(
    clean_embedding_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """jina 模式应从 EMBEDDING_DIMENSIONS / EMBEDDING_BATCH_SIZE 读取配置。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "jina")
    monkeypatch.setenv("JINA_API_KEY", "jina-test")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "768")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "8")

    config = get_embedding_config()

    assert config.dimensions == 768
    assert config.batch_size == 8


def test_get_embedding_config_jina_ignores_dashscope_key(
    clean_embedding_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """jina 模式应忽略 DASHSCOPE_API_KEY，只读 JINA_API_KEY。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "jina")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-ignored")
    monkeypatch.setenv("JINA_API_KEY", "jina-used")

    config = get_embedding_config()

    assert config.api_key == "jina-used"


# ---------------------------------------------------------------------------
# docker secrets 支持（阶段 11.6 切片 C：_FILE 后缀优先）
# ---------------------------------------------------------------------------


def test_get_embedding_config_dashscope_reads_api_key_from_file(
    clean_embedding_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``DASHSCOPE_API_KEY_FILE`` 指向文件时优先读文件内容。

    阶段 11.6 切片 C：docker secrets 通过 ``_FILE`` 后缀挂载密钥文件，
    即使 ``DASHSCOPE_API_KEY`` 环境变量也设置了，也用文件内容。
    """

    key_file = tmp_path / "dashscope_key.txt"
    key_file.write_text("sk-from-file\n", encoding="utf-8")

    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY_FILE", str(key_file))
    # 即使环境变量也设置了，_FILE 优先
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-env-should-be-ignored")

    config = get_embedding_config()

    assert config.api_key == "sk-from-file"


def test_get_embedding_config_jina_reads_api_key_from_file(
    clean_embedding_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``JINA_API_KEY_FILE`` 指向文件时优先读文件内容。"""

    key_file = tmp_path / "jina_key.txt"
    key_file.write_text("jina-from-file\n", encoding="utf-8")

    monkeypatch.setenv("EMBEDDING_PROVIDER", "jina")
    monkeypatch.setenv("JINA_API_KEY_FILE", str(key_file))
    # 即使环境变量也设置了，_FILE 优先
    monkeypatch.setenv("JINA_API_KEY", "jina-env-should-be-ignored")

    config = get_embedding_config()

    assert config.api_key == "jina-from-file"
