"""全局配置.

从 config.local.toml 读取配置（开发）或环境变量（生产）.
提供单例 Settings 供所有模块使用.
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
class ChatConfig:
    """对话模型配置."""

    base_url: str
    api_key: str
    main_model: str
    assist_model: str


@dataclass
class EmbeddingConfig:
    """嵌入模型配置."""

    base_url: str
    api_key: str
    model: str
    dimensions: int | None = None  # None = 由模型默认值决定


@dataclass
class RerankConfig:
    """重排序模型配置（可选）."""

    base_url: str
    api_key: str
    model: str


@dataclass
class StorageConfig:
    """存储路径配置."""

    memory_db_path: str = "data/memory.db"
    llm_db_path: str = "data/llm_service.db"
    auth_db_path: str = "data/auth.db"
    chroma_dir: str = "data/chroma"

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


@dataclass
class MemoryConfig:
    """记忆系统参数."""

    permanent_limit: int = 15
    permanent_load_top: int = 7
    retrieval_top_k: int = 5
    decay_batch_size: int = 50


@dataclass
class GraphConfig:
    """LangGraph 编排配置."""

    checkpoint_backend: str = "memory"  # memory | sqlite
    proxy_thinking_default: bool = False


@dataclass
class RuntimeConfig:
    """运行时配置."""

    host: str = "0.0.0.0"
    port: int = 16125
    log_level: str = "info"


@dataclass
class Settings:
    """全局配置聚合."""

    chat: ChatConfig
    embedding: EmbeddingConfig
    rerank: RerankConfig | None = None
    storage: StorageConfig = field(default_factory=StorageConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)


def _coerce_section(data: dict[str, Any], section: str) -> dict[str, Any]:
    """提取并校验一个配置段."""
    if section not in data:
        raise ValueError(f"config.local.toml 缺少 [{section}] 段")
    return data[section]


def load_settings() -> Settings:
    """加载配置.

    优先级: config.local.toml > 报错（必须有本地配置才能运行）.

    Returns:
        Settings 实例

    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: 配置项缺失或非法
    """
    if not LOCAL_CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"找不到 {LOCAL_CONFIG_PATH}\n"
            f"请复制模板: cp config.example.toml config.local.toml\n"
            f"然后填入真实凭证。"
        )

    with open(LOCAL_CONFIG_PATH, "rb") as f:
        data = tomllib.load(f)

    # chat
    chat_data = _coerce_section(data, "chat")
    chat = ChatConfig(
        base_url=chat_data["base_url"],
        api_key=chat_data["api_key"],
        main_model=chat_data["main_model"],
        assist_model=chat_data["assist_model"],
    )

    # embedding
    emb_data = _coerce_section(data, "embedding")
    embedding = EmbeddingConfig(
        base_url=emb_data["base_url"],
        api_key=emb_data["api_key"],
        model=emb_data["model"],
        dimensions=emb_data.get("dimensions"),
    )

    # rerank（可选）
    rerank: RerankConfig | None = None
    if "rerank" in data and data["rerank"]:
        rr = data["rerank"]
        if rr.get("base_url") and rr.get("api_key") and rr.get("model"):
            rerank = RerankConfig(
                base_url=rr["base_url"],
                api_key=rr["api_key"],
                model=rr["model"],
            )

    # storage
    storage = StorageConfig(**data.get("storage", {}))

    # memory
    memory = MemoryConfig(**data.get("memory", {}))

    # graph
    graph = GraphConfig(**data.get("graph", {}))

    # runtime
    runtime = RuntimeConfig(**data.get("runtime", {}))

    return Settings(
        chat=chat,
        embedding=embedding,
        rerank=rerank,
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
