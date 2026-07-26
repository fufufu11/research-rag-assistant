"""observability 单元测试（阶段 10.1 可观测性）。

测试覆盖：
- ``LangfuseConfig`` dataclass 字段
- ``load_langfuse_config_from_env``：三项非空 / 任一缺失 / 空白 strip
- ``is_langfuse_enabled``：与配置读取一致
- ``observe`` 装饰器：no-op 路径（透传）/ 启用路径（委托 langfuse.observe）
- ``get_current_langchain_handler``：未启用返回 None / 启用路径
- ``_build_run_config``：handler 与 extra_callbacks 组合
- ``flush``：未启用时 no-op

测试策略：
- 默认所有测试在未配置 ``LANGFUSE_*`` 环境变量下进行（CI 环境），验证 no-op 路径
- 启用路径通过 ``monkeypatch`` 设置环境变量 + ``sys.modules`` 注入 fake langfuse
  模块，避免依赖真实 Langfuse 服务

外部 LLM 调用全部通过 ``FakeListChatModel`` Mock，CI 不消耗真实 Token。
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from langchain_core.callbacks import BaseCallbackHandler

from research_rag.observability import (
    LangfuseConfig,
    _build_run_config,
    flush,
    get_current_langchain_handler,
    is_langfuse_enabled,
    load_langfuse_config_from_env,
    observe,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


# ---------------------------------------------------------------------------
# 辅助：清理 Langfuse 环境变量（确保默认 no-op 路径）
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_langfuse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """自动清理 Langfuse 环境变量，确保默认测试走 no-op 路径。

    同时清理 ``_FILE`` 后缀变量（阶段 11.6 切片 C：docker secrets 支持），
    避免上一个测试设置的文件路径污染下一个测试。
    """

    for name in (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_HOST",
        "LANGFUSE_PUBLIC_KEY_FILE",
        "LANGFUSE_SECRET_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)


def _set_langfuse_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    public_key: str = "pk-lf-test",
    secret_key: str = "sk-lf-test",
    host: str = "http://localhost:3000",
) -> None:
    """设置三项 Langfuse 环境变量（启用路径测试用）。"""

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", public_key)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", secret_key)
    monkeypatch.setenv("LANGFUSE_HOST", host)


# ---------------------------------------------------------------------------
# LangfuseConfig 与 load_langfuse_config_from_env
# ---------------------------------------------------------------------------


class TestLangfuseConfig:
    """``LangfuseConfig`` dataclass 与 ``load_langfuse_config_from_env`` 测试。"""

    def test_config_fields_immutable(self) -> None:
        """``LangfuseConfig`` 是 frozen dataclass，字段不可变。"""

        config = LangfuseConfig(public_key="pk-lf-x", secret_key="sk-lf-x", host="http://x")
        assert config.public_key == "pk-lf-x"
        assert config.secret_key == "sk-lf-x"
        assert config.host == "http://x"
        # FrozenInstanceError 继承自 AttributeError（dataclasses 文档）。
        with pytest.raises(AttributeError):
            config.public_key = "changed"  # type: ignore[misc]

    def test_load_config_returns_none_when_all_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """三项环境变量都未设置时返回 ``None``。"""

        assert load_langfuse_config_from_env() is None

    def test_load_config_returns_none_when_partial_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """任一环境变量缺失返回 ``None``（不完整配置不启用）。"""

        _set_langfuse_env(monkeypatch, public_key="", secret_key="sk", host="http://x")
        assert load_langfuse_config_from_env() is None

        _set_langfuse_env(monkeypatch, public_key="pk", secret_key="", host="http://x")
        assert load_langfuse_config_from_env() is None

        _set_langfuse_env(monkeypatch, public_key="pk", secret_key="sk", host="")
        assert load_langfuse_config_from_env() is None

    def test_load_config_returns_config_when_all_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """三项环境变量都非空时返回 ``LangfuseConfig``。"""

        _set_langfuse_env(monkeypatch)
        config = load_langfuse_config_from_env()
        assert config is not None
        assert config.public_key == "pk-lf-test"
        assert config.secret_key == "sk-lf-test"
        assert config.host == "http://localhost:3000"

    def test_load_config_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量前后空白字符被 strip（避免复制粘贴引入空格导致配置失败）。"""

        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "  pk-lf-test  ")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "\tsk-lf-test\n")
        monkeypatch.setenv("LANGFUSE_HOST", " http://localhost:3000 ")
        config = load_langfuse_config_from_env()
        assert config is not None
        assert config.public_key == "pk-lf-test"
        assert config.secret_key == "sk-lf-test"
        assert config.host == "http://localhost:3000"

    def test_load_config_reads_from_file_when_file_var_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``LANGFUSE_PUBLIC_KEY_FILE`` / ``LANGFUSE_SECRET_KEY_FILE`` 指向文件时优先读文件。

        阶段 11.6 切片 C：docker secrets 通过 ``_FILE`` 后缀挂载密钥文件，
        ``get_secret`` 优先读文件内容（strip 尾部换行），不读进程环境变量。
        ``LANGFUSE_HOST`` 不是密钥，仍从环境变量读取。
        """

        pub_file = tmp_path / "lf_pub.txt"
        pub_file.write_text("pk-from-file\n", encoding="utf-8")
        sec_file = tmp_path / "lf_sec.txt"
        sec_file.write_text("sk-from-file\n", encoding="utf-8")

        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY_FILE", str(pub_file))
        monkeypatch.setenv("LANGFUSE_SECRET_KEY_FILE", str(sec_file))
        # 即使环境变量也设置了，_FILE 优先
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-env-should-be-ignored")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-env-should-be-ignored")
        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")

        config = load_langfuse_config_from_env()
        assert config is not None
        assert config.public_key == "pk-from-file"
        assert config.secret_key == "sk-from-file"
        assert config.host == "http://localhost:3000"

    def test_load_config_falls_back_to_env_when_file_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``_FILE`` 指向不存在文件时 fallback 环境变量（开发/CI 兼容路径）。"""

        nonexistent_pub = tmp_path / "does-not-exist-pub.txt"
        nonexistent_sec = tmp_path / "does-not-exist-sec.txt"
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY_FILE", str(nonexistent_pub))
        monkeypatch.setenv("LANGFUSE_SECRET_KEY_FILE", str(nonexistent_sec))
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-env-fallback")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-env-fallback")
        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")

        config = load_langfuse_config_from_env()
        assert config is not None
        assert config.public_key == "pk-env-fallback"
        assert config.secret_key == "sk-env-fallback"


# ---------------------------------------------------------------------------
# is_langfuse_enabled
# ---------------------------------------------------------------------------


class TestIsEnabled:
    """``is_langfuse_enabled`` 与 ``load_langfuse_config_from_env`` 一致性。"""

    def test_disabled_by_default(self) -> None:
        """CI 默认未配置环境变量，``is_langfuse_enabled`` 返回 False。"""

        assert is_langfuse_enabled() is False

    def test_enabled_when_all_env_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """三项环境变量都设置后 ``is_langfuse_enabled`` 返回 True。"""

        _set_langfuse_env(monkeypatch)
        assert is_langfuse_enabled() is True

    def test_disabled_when_partial_env_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """任一环境变量缺失时 ``is_langfuse_enabled`` 返回 False。"""

        _set_langfuse_env(monkeypatch, secret_key="")
        assert is_langfuse_enabled() is False


# ---------------------------------------------------------------------------
# observe 装饰器
# ---------------------------------------------------------------------------


class TestObserveDecorator:
    """``observe`` 装饰器：no-op 路径透传 / 启用路径委托。"""

    def test_observe_noop_when_disabled(self) -> None:
        """未启用时装饰器透传原函数（不修改行为）。"""

        @observe("test.func")
        def add(a: int, b: int) -> int:
            return a + b

        assert add(1, 2) == 3

    def test_observe_noop_preserves_function_name(self) -> None:
        """no-op 路径下装饰后的函数名保持不变（便于调试与日志）。"""

        @observe("test.func")
        def my_function(x: int) -> int:
            """My docstring."""
            return x * 2

        assert my_function.__name__ == "my_function"
        assert my_function.__doc__ == "My docstring."

    def test_observe_noop_with_async_function(self) -> None:
        """no-op 路径支持异步函数（不破坏 await 行为）。"""

        @observe("test.async_func")
        async def fetch_value() -> int:
            await asyncio.sleep(0)
            return 42

        result = asyncio.run(fetch_value())
        assert result == 42

    def test_observe_noop_with_method(self) -> None:
        """no-op 路径支持类方法装饰（``self`` 透传）。"""

        class Calculator:
            @observe("test.method")
            def compute(self, x: int) -> int:
                return x + 10

        calc = Calculator()
        assert calc.compute(5) == 15

    def test_observe_delegates_to_langfuse_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """启用路径下委托给 ``langfuse.decorators.observe``。"""

        # 注入 fake langfuse.decorators 模块到 sys.modules
        @dataclass
        class FakeObserveCall:
            name: str

        observed_calls: list[FakeObserveCall] = []

        def fake_observe(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
            observed_calls.append(FakeObserveCall(name=name))

            def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
                def wrapper(*args: Any, **kwargs: Any) -> Any:
                    return f"observed:{func(*args, **kwargs)}"

                return wrapper

            return decorator

        fake_module = MagicMock()
        fake_module.observe = fake_observe
        monkeypatch.setitem(sys.modules, "langfuse", MagicMock())
        monkeypatch.setitem(sys.modules, "langfuse.decorators", fake_module)

        _set_langfuse_env(monkeypatch)

        @observe("test.delegated")
        def echo(x: int) -> int:
            return x

        # 装饰器应调用 langfuse.decorators.observe
        assert len(observed_calls) == 1
        assert observed_calls[0].name == "test.delegated"
        # 装饰后的函数走 langfuse 路径（wrapper 包裹）
        assert echo(123) == "observed:123"


# ---------------------------------------------------------------------------
# get_current_langchain_handler
# ---------------------------------------------------------------------------


class TestGetCurrentLangchainHandler:
    """``get_current_langchain_handler``：未启用返回 None / 启用路径委托。"""

    def test_returns_none_when_disabled(self) -> None:
        """未启用 Langfuse 时返回 ``None``。"""

        assert get_current_langchain_handler() is None

    def test_returns_none_outside_observe_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """启用 Langfuse 但不在 ``@observe`` 装饰的函数内时返回 ``None``。

        ``langfuse_context.get_current_langchain_handler`` 在无 trace 上下文时
        返回 ``None``，本项目函数直接透传该行为。
        """

        fake_context = MagicMock()
        fake_context.get_current_langchain_handler.return_value = None
        fake_module = MagicMock()
        fake_module.langfuse_context = fake_context
        monkeypatch.setitem(sys.modules, "langfuse", MagicMock())
        monkeypatch.setitem(sys.modules, "langfuse.decorators", fake_module)

        _set_langfuse_env(monkeypatch)
        assert get_current_langchain_handler() is None

    def test_returns_handler_inside_observe_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """启用 Langfuse 且在 trace 上下文内时返回 handler。"""

        fake_handler = MagicMock(spec=BaseCallbackHandler)
        fake_context = MagicMock()
        fake_context.get_current_langchain_handler.return_value = fake_handler
        fake_module = MagicMock()
        fake_module.langfuse_context = fake_context
        monkeypatch.setitem(sys.modules, "langfuse", MagicMock())
        monkeypatch.setitem(sys.modules, "langfuse.decorators", fake_module)

        _set_langfuse_env(monkeypatch)
        assert get_current_langchain_handler() is fake_handler


# ---------------------------------------------------------------------------
# _build_run_config
# ---------------------------------------------------------------------------


class TestBuildRunConfig:
    """``_build_run_config`` 工具函数：构造 LangChain RunnableConfig。"""

    def test_none_handler_and_none_callbacks_returns_none(self) -> None:
        """handler 与 extra_callbacks 都为 None 时返回 ``None``（不构造空 config）。"""

        assert _build_run_config(None, None) is None
        assert _build_run_config(None, []) is None  # type: ignore[arg-type]

    def test_only_handler(self) -> None:
        """只有 handler 时返回 ``{"callbacks": [handler]}``。"""

        handler = MagicMock(spec=BaseCallbackHandler)
        config = _build_run_config(handler, None)
        assert config == {"callbacks": [handler]}

    def test_only_extra_callbacks(self) -> None:
        """只有 extra_callbacks 时返回 ``{"callbacks": [...]}``。"""

        cb1 = MagicMock(spec=BaseCallbackHandler)
        cb2 = MagicMock(spec=BaseCallbackHandler)
        config = _build_run_config(None, [cb1, cb2])
        assert config == {"callbacks": [cb1, cb2]}

    def test_handler_and_extra_callbacks_combined(self) -> None:
        """handler 与 extra_callbacks 合并到同一 callbacks 列表。"""

        handler = MagicMock(spec=BaseCallbackHandler)
        extra = MagicMock(spec=BaseCallbackHandler)
        config = _build_run_config(handler, [extra])
        assert config == {"callbacks": [handler, extra]}

    def test_handler_order_preserved(self) -> None:
        """handler 在前，extra_callbacks 在后（顺序可被 Langfuse trace 关联依赖）。"""

        handler = MagicMock(spec=BaseCallbackHandler, name="handler")
        cb1 = MagicMock(spec=BaseCallbackHandler, name="cb1")
        cb2 = MagicMock(spec=BaseCallbackHandler, name="cb2")
        config = _build_run_config(handler, [cb1, cb2])
        callbacks = config["callbacks"] if config else []
        assert callbacks == [handler, cb1, cb2]


# ---------------------------------------------------------------------------
# flush
# ---------------------------------------------------------------------------


class TestFlush:
    """``flush`` 函数：未启用 no-op / 启用路径委托。"""

    def test_flush_noop_when_disabled(self) -> None:
        """未启用 Langfuse 时 ``flush`` 不报错（no-op）。"""

        # 不应抛出任何异常
        flush()

    def test_flush_calls_langfuse_context_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """启用 Langfuse 时 ``flush`` 调用 ``langfuse_context.flush``。"""

        fake_context = MagicMock()
        fake_context.flush = MagicMock()
        fake_module = MagicMock()
        fake_module.langfuse_context = fake_context
        monkeypatch.setitem(sys.modules, "langfuse", MagicMock())
        monkeypatch.setitem(sys.modules, "langfuse.decorators", fake_module)

        _set_langfuse_env(monkeypatch)
        flush()
        fake_context.flush.assert_called_once()


# ---------------------------------------------------------------------------
# 集成：QaService.answer 在未启用 Langfuse 时行为不变
# ---------------------------------------------------------------------------


class TestQaServiceLangfuseNoopIntegration:
    """``QaService.answer`` 在未启用 Langfuse 时行为与阶段 9.2 完全一致。

    确保 ``@observe`` 装饰器与 ``run_config=None`` 透传不破坏既有问答流程。
    复用阶段 9.2 测试的 FakeListChatModel + 内存 DB 模式。
    """

    def test_answer_returns_correct_response_without_langfuse(self) -> None:
        """未启用 Langfuse 时 ``QaService.answer`` 返回正常答案。"""

        import hashlib

        from langchain_core.embeddings import Embeddings
        from langchain_core.language_models.fake_chat_models import FakeListChatModel
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session, sessionmaker

        from research_rag.db.models import Base, Chunk, Document, DocumentStatus
        from research_rag.embedding import EmbeddingConfig
        from research_rag.qa_service import LlmConfig
        from research_rag.services.qa_service import QaService

        class _FakeEmbeddings(Embeddings):
            """确定性字符袋 Embeddings（与 test_qa_orchestration 一致）。"""

            def __init__(self, dim: int = 64) -> None:
                self.dim = dim

            def _embed_one(self, text: str) -> list[float]:
                vec = [0.0] * self.dim
                for char in text:
                    vec[ord(char) % self.dim] += 1.0
                norm = sum(v * v for v in vec) ** 0.5
                if norm > 0:
                    vec = [v / norm for v in vec]
                return vec

            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                return [self._embed_one(t) for t in texts]

            def embed_query(self, text: str) -> list[float]:
                return self._embed_one(text)

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        session: Session = factory()

        try:
            # 构造 1 个 READY 文档 + 1 个 Chunk
            doc = Document(
                original_name="paper1.pdf",
                stored_name="paper1.pdf.stored",
                sha256=hashlib.sha256(b"paper1.pdf").hexdigest(),
                page_count=1,
                status=DocumentStatus.READY,
            )
            session.add(doc)
            session.flush()
            session.add(
                Chunk(
                    document_id=doc.id,
                    start_page=1,
                    end_page=1,
                    chunk_index=0,
                    content="答案是 42。",
                    char_count=len("答案是 42。"),
                )
            )
            session.commit()

            chat_model = FakeListChatModel(responses=["根据 [C1] 文档可知答案是 42。"])
            service = QaService(
                session,
                LlmConfig(api_key="fake", model="fake-model"),
                embedding_config=EmbeddingConfig(model_name="fake"),
                embeddings=_FakeEmbeddings(),
                chat_model=chat_model,
            )

            response = service.answer("问题", document_ids=[doc.id])
            assert response.answer == "根据 [C1] 文档可知答案是 42。"
            assert len(response.citations) == 1
            assert response.citations[0].document_name == "paper1.pdf"
        finally:
            session.close()
            engine.dispose()

    def test_answer_works_with_run_config_none_transparently(self) -> None:
        """``run_config=None`` 透传给 ``chat_model.invoke`` 不影响行为。

        直接验证 ``answer_question`` 接受 ``run_config=None`` 与不传等价。
        """

        from langchain_core.language_models.fake_chat_models import FakeListChatModel

        from research_rag.qa_service import (
            ContextPiece,
            answer_question,
        )

        contexts = [
            ContextPiece(
                document_name="doc.pdf",
                start_page=1,
                end_page=1,
                chunk_index=0,
                content="答案是 42。",
            )
        ]
        chat_model = FakeListChatModel(responses=["答案是 42。"])

        # 不传 run_config（默认 None）
        result1 = answer_question("问题", contexts, chat_model)
        # 显式传 run_config=None
        result2 = answer_question("问题", contexts, chat_model, run_config=None)

        assert result1.answer_text == "答案是 42。"
        assert result2.answer_text == "答案是 42。"
