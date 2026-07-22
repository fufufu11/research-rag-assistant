"""Streamlit 演示界面包（阶段 6 第二个 Issue）。

UI 层只通过 HTTP 调用 FastAPI API，不直接 import 业务层（service/repository），
保持分层清晰：``ui`` → ``HTTP`` → ``api`` → ``services`` → ``db``。
"""
