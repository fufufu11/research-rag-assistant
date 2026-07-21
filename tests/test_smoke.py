"""最小冒烟测试。

仅验证：
1. 包可被导入
2. pytest 能正常运行

业务测试随各阶段 Issue 逐步添加。
"""

from research_rag import __version__


def test_version_is_string() -> None:
    """版本号应为字符串，且非空。"""
    assert isinstance(__version__, str)
    assert __version__ != ""


def test_package_importable() -> None:
    """research_rag 包应可被正常导入。"""
    import research_rag

    assert research_rag is not None
