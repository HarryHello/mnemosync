"""全局配置.

从 config.local.toml 读取配置（开发）或环境变量（生产）.
提供单例 Settings 供所有模块使用.

v0.2.3 起, chat/embedding/rerank 模型不再来自本文件, 而是由
llm_service.db 的 role_bindings 表管理 (由 UI/CLI 增删改).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 本地配置文件路径（被 gitignore 忽略）
LOCAL_CONFIG_PATH = PROJECT_ROOT / "config.local.toml"


@dataclass
class StorageConfig:
    """存储路径配置."""

    memory_db_path: str = "data/memory.db"
    llm_db_path: str = "data/llm_service.db"
    auth_db_path: str = "data/auth.db"
    chroma_dir: str = "data/chroma"
    prompts_override_dir: str = "data/prompts"
    conversation_db_path: str = "data/conversation.db"
    short_term_days: int = 7

    @property
    def memory_db_abs(self) -> Path:
        return PROJECT_ROOT / self.memory_db_path

    @property
    def llm_db_abs(self) -> Path:
        return PROJECT_ROOT / self.llm_db_path

    @property
    def auth_db_abs(self) -> Path:
        return PROJECT_ROOT / self.auth_db_path

    @property
    def chroma_dir_abs(self) -> Path:
        return PROJECT_ROOT / self.chroma_dir

    @property
    def prompts_override_dir_abs(self) -> Path:
        return PROJECT_ROOT / self.prompts_override_dir

    @property
    def conversation_db_abs(self) -> Path:
        return PROJECT_ROOT / self.conversation_db_path


@dataclass
class MemoryConfig:
    """记忆系统参数."""

    permanent_limit: int = 15
    permanent_load_top: int = 7
    retrieval_top_k: int = 5
    decay_batch_size: int = 50


DEFAULT_NATIVE_REASONING_MODELS: tuple[str, ...] = (
    "o1*",
    "o3*",
    "o4*",
    "deepseek-r1*",
    "deepseek-reasoner*",
    "qwen3-*-thinking",
    "qwq*",
    "gpt-5-thinking-*",
)


@dataclass
class PersonaConfig:
    """服务器人格配置 (server-first: 人格由服务器端权威定义)."""

    name: str = "助手"
    prompt: str = "你是一个温暖、有记忆能力的 AI 助手。"


@dataclass
class GraphConfig:
    """LangGraph 编排配置."""

    checkpoint_backend: str = "memory"  # memory | sqlite
    proxy_thinking_default: bool = False
    proxy_thinking_native_reasoning_models: list[str] = field(
        default_factory=lambda: list(DEFAULT_NATIVE_REASONING_MODELS)
    )


@dataclass
class RuntimeConfig:
    """运行时配置."""

    host: str = "0.0.0.0"
    port: int = 16125
    log_level: str = "info"


@dataclass
class Settings:
    """全局配置聚合.

    v0.2.3 起, 模型绑定 (chat/embedding/rerank) 由 role_bindings 表管理,
    本类只保留纯粹的应用参数.
    """

    persona: PersonaConfig = field(default_factory=PersonaConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)


def load_settings() -> Settings:
    """加载配置.

    优先级: config.local.toml > 缺失时使用全默认值 (不再必需文件存在).
    """
    if not LOCAL_CONFIG_PATH.exists():
        # 无本地配置也允许启动 (v0.2.3 起模型绑定不再依赖配置文件)
        return Settings()

    with open(LOCAL_CONFIG_PATH, "rb") as f:
        data = tomllib.load(f)

    storage = StorageConfig(**data.get("storage", {}))
    persona = PersonaConfig(**data.get("persona", {}))
    memory = MemoryConfig(**data.get("memory", {}))
    graph = GraphConfig(**data.get("graph", {}))
    runtime = RuntimeConfig(**data.get("runtime", {}))

    return Settings(
        persona=persona,
        storage=storage,
        memory=memory,
        graph=graph,
        runtime=runtime,
    )


# 全局单例（首次访问时加载）
_settings: Settings | None = None


def get_settings() -> Settings:
    """获取全局 Settings 单例.

    首次调用时加载配置文件; 后续调用返回缓存.
    测试时可用 _reset_settings() 重置.
    """
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def _reset_settings() -> None:
    """重置全局单例（仅供测试用）."""
    global _settings
    _settings = None
