"""输入栏「+」上传按钮 + 免责声明（Issue #112）的纯函数单元测试。

测试范围：
- ``_is_valid_pdf_filename``：上传文件名 PDF 扩展名校验
- ``_UPLOAD_DISCLAIMER``：免责声明文案常量

UI 交互部分（popover 渲染、按钮位置）靠手动验证，不在此测试覆盖内。
"""

from __future__ import annotations

import research_rag.ui.app as app_module

# ---------------------------------------------------------------------------
# _is_valid_pdf_filename
# ---------------------------------------------------------------------------


def test_pdf_lowercase_extension_is_valid() -> None:
    assert app_module._is_valid_pdf_filename("paper.pdf") is True


def test_pdf_uppercase_extension_is_valid() -> None:
    assert app_module._is_valid_pdf_filename("paper.PDF") is True


def test_pdf_mixed_case_extension_is_valid() -> None:
    assert app_module._is_valid_pdf_filename("Paper.PdF") is True


def test_txt_extension_is_invalid() -> None:
    assert app_module._is_valid_pdf_filename("notes.txt") is False


def test_no_extension_is_invalid() -> None:
    assert app_module._is_valid_pdf_filename("paper") is False


def test_empty_string_is_invalid() -> None:
    assert app_module._is_valid_pdf_filename("") is False


def test_filename_with_spaces_and_pdf_is_valid() -> None:
    assert app_module._is_valid_pdf_filename("my paper final.pdf") is True


# ---------------------------------------------------------------------------
# _UPLOAD_DISCLAIMER
# ---------------------------------------------------------------------------


def test_upload_disclaimer_is_non_empty_string() -> None:
    assert isinstance(app_module._UPLOAD_DISCLAIMER, str)
    assert len(app_module._UPLOAD_DISCLAIMER) > 0
