"""secrets helper 单元测试（阶段 11.6 切片 A，Issue #97）。

测试覆盖 ``get_secret(name)`` 的五条行为路径：

1. fallback env：无 ``{name}_FILE`` 时返回 ``os.environ.get(name)``
2. env 未设置返回 ``None``：无 ``_FILE`` 且 env 未设置
3. ``_FILE`` 优先：``{name}_FILE`` 指向可读文件时返回文件内容（strip 尾部换行）
4. 文件不存在 fallback env：``{name}_FILE`` 指向不存在文件时 fallback env
5. 文件为空返回空字符串：``{name}_FILE`` 指向空文件时返回 ``""``（不 fallback）

测试策略：
- 用 ``tmp_path`` fixture 创建临时 secrets 文件，避免污染工作区
- 用 ``monkeypatch.setenv`` / ``monkeypatch.delenv`` 控制环境变量
- 每个 test class 自动清理 ``TEST_KEY`` 与 ``TEST_KEY_FILE`` 环境变量
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from research_rag.secrets import get_secret

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# 辅助：自动清理测试用的环境变量
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """自动清理测试用的环境变量，确保每个测试从干净状态开始。"""

    monkeypatch.delenv("TEST_KEY", raising=False)
    monkeypatch.delenv("TEST_KEY_FILE", raising=False)


# ---------------------------------------------------------------------------
# 行为 1：fallback env（无 _FILE 时返回 os.environ.get(name)）
# ---------------------------------------------------------------------------


class TestGetSecretFallbackEnv:
    """无 ``{name}_FILE`` 时 ``get_secret`` 回退到环境变量。"""

    def test_returns_env_value_when_no_file_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无 ``TEST_KEY_FILE`` 时返回 ``os.environ.get("TEST_KEY")`` 的值。"""

        monkeypatch.setenv("TEST_KEY", "env-value-123")
        assert get_secret("TEST_KEY") == "env-value-123"

    def test_returns_none_when_env_missing_and_no_file_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无 ``TEST_KEY_FILE`` 且 ``TEST_KEY`` 未设置时返回 ``None``。"""

        # autouse fixture 已清理 TEST_KEY 与 TEST_KEY_FILE
        assert get_secret("TEST_KEY") is None


# ---------------------------------------------------------------------------
# 行为 3：_FILE 优先（{name}_FILE 指向可读文件时返回文件内容 strip 尾部换行）
# ---------------------------------------------------------------------------


class TestGetSecretFilePriority:
    """``{name}_FILE`` 指向可读文件时 ``get_secret`` 返回文件内容。"""

    def test_returns_file_content_when_file_var_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``TEST_KEY_FILE`` 指向可读文件时返回文件内容（strip 尾部换行）。

        docker secrets 文件挂载默认带尾部换行，``strip()`` 避免密钥末尾多 ``\\n``。
        """

        secrets_file = tmp_path / "test_key.txt"
        secrets_file.write_text("file-secret-456\n", encoding="utf-8")
        monkeypatch.setenv("TEST_KEY_FILE", str(secrets_file))
        # 即使 TEST_KEY 也设置了，_FILE 优先
        monkeypatch.setenv("TEST_KEY", "env-value-should-be-ignored")
        assert get_secret("TEST_KEY") == "file-secret-456"


# ---------------------------------------------------------------------------
# 行为 4：文件不存在 fallback env（{name}_FILE 指向不存在文件时回退环境变量）
# ---------------------------------------------------------------------------


class TestGetSecretFileMissingFallback:
    """``{name}_FILE`` 指向不存在文件时 ``get_secret`` 回退到环境变量。"""

    def test_falls_back_to_env_when_file_not_found(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``TEST_KEY_FILE`` 指向不存在的文件时 fallback ``os.environ.get("TEST_KEY")``。

        生产环境若 ``_FILE`` 路径配错，fallback env 让开发环境（无 secrets 文件）
        仍能工作；后续业务逻辑读到空字符串报错暴露问题。
        """

        nonexistent = tmp_path / "does-not-exist.txt"
        monkeypatch.setenv("TEST_KEY_FILE", str(nonexistent))
        monkeypatch.setenv("TEST_KEY", "env-fallback-789")
        assert get_secret("TEST_KEY") == "env-fallback-789"

    def test_returns_none_when_file_not_found_and_env_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``TEST_KEY_FILE`` 指向不存在文件且 ``TEST_KEY`` 未设置时返回 ``None``。"""

        nonexistent = tmp_path / "does-not-exist.txt"
        monkeypatch.setenv("TEST_KEY_FILE", str(nonexistent))
        # autouse fixture 已清理 TEST_KEY
        assert get_secret("TEST_KEY") is None


# ---------------------------------------------------------------------------
# 行为 5：文件为空返回空字符串（不 fallback env，文件存在即视为已配置）
# ---------------------------------------------------------------------------


class TestGetSecretEmptyFile:
    """``{name}_FILE`` 指向空文件时 ``get_secret`` 返回空字符串。"""

    def test_returns_empty_string_when_file_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``TEST_KEY_FILE`` 指向空文件时返回 ``""``，不 fallback env。

        文件存在但内容为空是合法的（读取成功），不触发 fallback；
        与"文件不存在"（读取失败）区分。
        """

        empty_file = tmp_path / "empty.txt"
        empty_file.write_text("", encoding="utf-8")
        monkeypatch.setenv("TEST_KEY_FILE", str(empty_file))
        # 即使 TEST_KEY 也设置了，文件存在即优先，不 fallback
        monkeypatch.setenv("TEST_KEY", "env-value-should-be-ignored")
        assert get_secret("TEST_KEY") == ""

    def test_returns_empty_string_when_file_only_whitespace(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``TEST_KEY_FILE`` 文件只含空白字符时 ``strip()`` 后返回 ``""``。"""

        whitespace_file = tmp_path / "whitespace.txt"
        whitespace_file.write_text("   \n\t\n", encoding="utf-8")
        monkeypatch.setenv("TEST_KEY_FILE", str(whitespace_file))
        assert get_secret("TEST_KEY") == ""
