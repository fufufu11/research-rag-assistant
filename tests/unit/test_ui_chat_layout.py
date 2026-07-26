"""对话区居中 + 用户消息右对齐视觉调整（Issue #111）的纯函数单元测试。

测试范围：
- ``_get_chat_layout_css``：生成对话区居中布局的 CSS 字符串

视觉效果（CSS 实际注入后渲染）靠手动验证，不在此测试覆盖内。
"""

from __future__ import annotations

import research_rag.ui.app as app_module

# ---------------------------------------------------------------------------
# _get_chat_layout_css
# ---------------------------------------------------------------------------


def test_chat_layout_css_is_non_empty_string() -> None:
    css = app_module._get_chat_layout_css()
    assert isinstance(css, str)
    assert len(css) > 0


def test_chat_layout_css_contains_max_width() -> None:
    css = app_module._get_chat_layout_css()
    assert "max-width" in css


def test_chat_layout_css_contains_default_width_800px() -> None:
    css = app_module._get_chat_layout_css()
    assert "800px" in css


def test_chat_layout_css_custom_width() -> None:
    css = app_module._get_chat_layout_css(max_width_px=900)
    assert "900px" in css


def test_chat_layout_css_contains_margin_auto_for_centering() -> None:
    css = app_module._get_chat_layout_css()
    assert "auto" in css.lower()
