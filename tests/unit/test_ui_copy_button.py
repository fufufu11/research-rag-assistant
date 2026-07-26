"""AI 回复复制按钮（Issue #113）的纯函数单元测试。

测试范围：
- ``_strip_markdown_to_plain_text``：去除 markdown 标记，提取纯文本

复制按钮的 JS 注入与 toast 提示靠手动验证，不在此测试覆盖内。
"""

from __future__ import annotations

import research_rag.ui.app as app_module

# ---------------------------------------------------------------------------
# _strip_markdown_to_plain_text
# ---------------------------------------------------------------------------


def test_plain_text_unchanged() -> None:
    assert app_module._strip_markdown_to_plain_text("普通文本") == "普通文本"


def test_bold_markdown_stripped() -> None:
    assert app_module._strip_markdown_to_plain_text("**重点**") == "重点"


def test_italic_markdown_stripped() -> None:
    assert app_module._strip_markdown_to_plain_text("*斜体*") == "斜体"


def test_link_markdown_stripped_to_text() -> None:
    assert app_module._strip_markdown_to_plain_text("[论文](http://example.com)") == "论文"


def test_heading_markdown_stripped() -> None:
    assert app_module._strip_markdown_to_plain_text("# 标题") == "标题"


def test_inline_code_markdown_stripped() -> None:
    assert app_module._strip_markdown_to_plain_text("`code`") == "code"


def test_empty_string_returns_empty() -> None:
    assert app_module._strip_markdown_to_plain_text("") == ""


def test_mixed_markdown_stripped() -> None:
    text = "# 标题\n\n这是 **重点** 和 [链接](http://x) 及 `code`"
    expected = "标题\n\n这是 重点 和 链接 及 code"
    assert app_module._strip_markdown_to_plain_text(text) == expected
