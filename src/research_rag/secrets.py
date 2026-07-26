"""密钥读取统一 helper（阶段 11.6 切片 A，Issue #97）。

提供 ``get_secret(name)`` 函数，先读 ``{name}_FILE`` 环境变量指向的文件内容，
再 fallback ``os.environ.get(name)``。保持向后兼容：

- 开发与 CI 用环境变量不受影响
- 生产用 ``_FILE`` 后缀挂载 docker secrets，secrets 不进进程环境变量

决策见 ADR 0004（``docs/adr/0004-docker-secrets-helper.md``）。
"""

from __future__ import annotations

import os


def get_secret(name: str) -> str | None:
    """读取密钥值，优先从 ``{name}_FILE`` 文件读取，fallback 环境变量。

    Args:
        name: 密钥名（如 ``"LLM_API_KEY"``）。函数会先查 ``{name}_FILE``
            环境变量，若存在且指向可读文件，返回文件内容（strip 尾部换行）；
            否则 fallback ``os.environ.get(name)``。

    Returns:
        密钥值字符串，或 ``None``（未配置）。
    """

    file_path = os.environ.get(f"{name}_FILE")
    if file_path:
        try:
            with open(file_path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            pass  # 文件不可读，fallback 到环境变量

    return os.environ.get(name)
