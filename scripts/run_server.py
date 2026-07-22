"""本地开发服务器启动脚本（加载 .env 环境变量后启动 uvicorn）。

用途：项目未集成 python-dotenv，本脚本手动解析 .env 文件并设置环境变量，
然后启动 uvicorn。仅用于本地端到端测试，非生产入口。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


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

    import uvicorn

    uvicorn.run(
        "research_rag.api.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )


if __name__ == "__main__":
    sys.exit(main())
