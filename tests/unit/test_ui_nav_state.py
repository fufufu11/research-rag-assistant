"""左侧导航重构（Issue #109）的纯函数单元测试。

测试范围：
- 导航分组折叠状态判定（``_is_nav_section_expanded``）
- 左侧栏整体收起状态判定（``_is_sidebar_collapsed``）

视觉布局部分（图标渲染、分组分隔、expander 外观）靠手动验证，
不在此测试覆盖内。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import MutableMapping


def _make_session_state(initial: dict[str, object] | None = None) -> MutableMapping[str, object]:
    """构造一个普通 dict 模拟 Streamlit ``st.session_state``。"""

    return dict(initial or {})


# ---------------------------------------------------------------------------
# _is_nav_section_expanded
# ---------------------------------------------------------------------------


def test_history_section_expanded_by_default() -> None:
    """历史会话列表分组默认展开。"""

    from research_rag.ui.app import _is_nav_section_expanded

    assert _is_nav_section_expanded("history", _make_session_state()) is True


def test_docs_section_expanded_by_default() -> None:
    """文档列表分组默认展开。"""

    from research_rag.ui.app import _is_nav_section_expanded

    assert _is_nav_section_expanded("docs", _make_session_state()) is True


def test_history_section_collapsed_when_session_state_false() -> None:
    """session_state 中显式设为 False 时，历史会话分组折叠。"""

    from research_rag.ui.app import _is_nav_section_expanded

    state = _make_session_state({"nav-history-expanded": False})
    assert _is_nav_section_expanded("history", state) is False


def test_unknown_section_collapsed_by_default() -> None:
    """未知 key 的分组默认折叠。"""

    from research_rag.ui.app import _is_nav_section_expanded

    assert _is_nav_section_expanded("unknown", _make_session_state()) is False


# ---------------------------------------------------------------------------
# _is_sidebar_collapsed
# ---------------------------------------------------------------------------


def test_sidebar_not_collapsed_by_default() -> None:
    """左侧栏默认不折叠（展开显示）。"""

    from research_rag.ui.app import _is_sidebar_collapsed

    assert _is_sidebar_collapsed(_make_session_state()) is False


def test_sidebar_collapsed_when_session_state_true() -> None:
    """session_state 中显式设为 True 时，左侧栏折叠。"""

    from research_rag.ui.app import _is_sidebar_collapsed

    state = _make_session_state({"sidebar-collapsed": True})
    assert _is_sidebar_collapsed(state) is True
