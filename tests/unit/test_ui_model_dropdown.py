"""顶部模型选择下拉（Issue #110）的纯函数单元测试。

测试范围：
- ``_get_current_model_name``：从 ``LLM_MODEL`` 环境变量读取模型名，未设置时
  fallback 到占位字符串 ``"research-rag"``
- ``_get_model_dropdown_options``：构造占位下拉选项列表（单元素）

视觉布局部分（``st.selectbox`` 渲染、disabled 状态、位置）靠手动验证，
不在此测试覆盖内。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# _get_current_model_name
# ---------------------------------------------------------------------------


def test_current_model_name_fallback_when_env_unset(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """``LLM_MODEL`` 环境变量未设置时，返回占位字符串 ``"research-rag"``。"""

    monkeypatch.delenv("LLM_MODEL", raising=False)
    from research_rag.ui.app import _get_current_model_name

    assert _get_current_model_name() == "research-rag"


def test_current_model_name_from_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """``LLM_MODEL`` 环境变量有值时，返回该值。"""

    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    from research_rag.ui.app import _get_current_model_name

    assert _get_current_model_name() == "gpt-4o-mini"


def test_current_model_name_fallback_when_env_empty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """``LLM_MODEL`` 环境变量为空字符串时，fallback 到占位字符串。"""

    monkeypatch.setenv("LLM_MODEL", "")
    from research_rag.ui.app import _get_current_model_name

    assert _get_current_model_name() == "research-rag"


# ---------------------------------------------------------------------------
# _get_model_dropdown_options
# ---------------------------------------------------------------------------


def test_model_dropdown_options_single_item() -> None:
    """占位下拉只返回一个选项（不提供真切换）。"""

    from research_rag.ui.app import _get_model_dropdown_options

    options = _get_model_dropdown_options("research-rag")
    assert len(options) == 1


def test_model_dropdown_options_contains_current_model_prefix() -> None:
    """选项文案包含「当前模型：」前缀，明确告知用户这是当前在用模型。"""

    from research_rag.ui.app import _get_model_dropdown_options

    options = _get_model_dropdown_options("gpt-4o-mini")
    assert options[0].startswith("当前模型：")


def test_model_dropdown_options_includes_model_name() -> None:
    """选项文案包含传入的模型名。"""

    from research_rag.ui.app import _get_model_dropdown_options

    options = _get_model_dropdown_options("gpt-4o-mini")
    assert "gpt-4o-mini" in options[0]
