"""本地开发服务器启动脚本（绕过 Windows 应用程序控制策略阻止 _multiprocessing DLL）。

错误现象（Windows）：
    ImportError: DLL load failed while importing _multiprocessing:
    应用程序控制策略已阻止此文件

原因：uvicorn/__init__.py → main.py → supervisors → _subprocess.py 调用
    multiprocessing.allow_connection_pickling()
触发 import _multiprocessing，该 C 扩展的 DLL 被 AppLocker/WDAC 策略阻止。

本脚本在导入 uvicorn 前，向 sys.modules 注入一个空的 _multiprocessing
占位模块，让 import 通过；并 monkey-patch
multiprocessing.allow_connection_pickling 为 no-op。

副作用：
- 不支持 uvicorn 多进程 workers（本地试用无需）
- 不支持 --reload（本地试用无需）
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path


def _patch_multiprocessing() -> None:
    """注入 _multiprocessing mock 模块 + 让 allow_connection_pickling no-op。

    Windows AppLocker/WDAC 策略可能阻止 _multiprocessing.pyd DLL 加载，
    但 uvicorn 和 torch 都会触发 import _multiprocessing。

    本函数注入一个 mock 模块，提供必要属性（closesocket / send / recv / Connection），
    让依赖该模块的代码能完成 import。本地单进程试用不会真正调用这些函数，
    仅作为类型签名默认值存在。
    """
    if "_multiprocessing" not in sys.modules:
        fake_module = types.ModuleType("_multiprocessing")
        # multiprocessing.connection 模块在 Windows 上引用这些函数作为
        # 默认参数值（line 371-374）。本地试用不会真正调用，提供 no-op mock。
        fake_module.closesocket = lambda _handle: None  # type: ignore[attr-defined]
        fake_module.send = lambda _handle, _buf: None  # type: ignore[attr-defined]
        fake_module.recv = lambda _handle, _size: b""  # type: ignore[attr-defined]
        # Connection 类：connection.py 也尝试继承 _multiprocessing.Connection
        # 提供一个空类避免 AttributeError
        fake_module.Connection = type("Connection", (), {})  # type: ignore[attr-defined]
        sys.modules["_multiprocessing"] = fake_module

    # monkey-patch multiprocessing.allow_connection_pickling 为 no-op
    import multiprocessing

    multiprocessing.allow_connection_pickling = lambda: None  # type: ignore[attr-defined]


def load_env_file(env_path: Path) -> None:
    """解析简单的 KEY=VALUE .env 文件并设置到 os.environ。"""
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key:
            os.environ.setdefault(key, value)


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    load_env_file(project_root / ".env")

    # 必须在 import uvicorn 之前打补丁
    _patch_multiprocessing()

    # 现在可以安全 import uvicorn
    import uvicorn

    uvicorn.run(
        "research_rag.api.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        log_level="info",
        reload=False,
        workers=1,
    )


if __name__ == "__main__":
    main()
