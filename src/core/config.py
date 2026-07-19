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

# 人格 override 文件路径 (由面板 PUT /panel/admin/persona 写入, 优先级最高)
PERSONA_OVERRIDE_PATH = PROJECT_ROOT / "data" / "persona_override.toml"


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
class RelationConfig:
    """人格与用户的关系框架 (记忆分析 / 关系分析 Agent 会看到).

    只放事实性字段, 不放主观形容; 用于让 Agent 把提取产物写成
    "哥哥 X" 而非通用的 "用户 X". 兜底值中立, 保证 [relation] 段缺失时不炸.
    """

    persona_addressing: str = "人格"
    user_addressing: str = "用户"
    context: str = "AI 助手与用户"


@dataclass
class PersonaConfig:
    """服务器人格配置 (server-first: 人格由服务器端权威定义).

    默认值来自 src/resources/personas/default.toml (打包进 wheel), 用户可通过
    config.local.toml 的 [persona] 段覆盖. 想改默认人格请改资源 TOML, 不要改本类字段.
    """

    name: str = field(default_factory=lambda: _load_default_persona()["name"])
    prompt: str = field(default_factory=lambda: _load_default_persona()["prompt"])
    relation: RelationConfig = field(
        default_factory=lambda: RelationConfig(**_load_default_persona()["relation"])
    )


_DEFAULT_PERSONA_PATH = Path(__file__).resolve().parent.parent / "resources" / "personas" / "default.toml"

# 极简兜底: 资源文件缺失时使用, 保持中立不带任何特定角色扮演
_FALLBACK_PERSONA: dict[str, Any] = {
    "name": "助手",
    "prompt": "你是一个有记忆能力的 AI 助手。",
    "relation": {
        "persona_addressing": "人格",
        "user_addressing": "用户",
        "context": "AI 助手与用户",
    },
}


def _load_default_persona() -> dict[str, Any]:
    """从打包资源 TOML 读默认人格.

    Returns:
        dict, 键 name / prompt / relation. relation 是嵌套 dict, 字段与 RelationConfig 对齐.

    兜底: 文件缺失、字段缺失、或 TOML 解析失败时用 _FALLBACK_PERSONA 补齐.
    """
    try:
        with open(_DEFAULT_PERSONA_PATH, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {
            "name": _FALLBACK_PERSONA["name"],
            "prompt": _FALLBACK_PERSONA["prompt"],
            "relation": dict(_FALLBACK_PERSONA["relation"]),
        }

    name = str(data.get("name") or _FALLBACK_PERSONA["name"])
    prompt = str(data.get("prompt") or _FALLBACK_PERSONA["prompt"])
    raw_rel = data.get("relation") or {}
    fallback_rel = _FALLBACK_PERSONA["relation"]
    relation = {
        "persona_addressing": str(raw_rel.get("persona_addressing") or fallback_rel["persona_addressing"]),
        "user_addressing": str(raw_rel.get("user_addressing") or fallback_rel["user_addressing"]),
        "context": str(raw_rel.get("context") or fallback_rel["context"]),
    }
    return {"name": name, "prompt": prompt, "relation": relation}


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


def _load_persona_override() -> dict[str, Any] | None:
    """从 data/persona_override.toml 读取面板写入的人格覆盖.

    Returns:
        dict {name?, prompt?, relation?} 或 None (文件不存在 / 解析失败).
    """
    if not PERSONA_OVERRIDE_PATH.exists():
        return None
    try:
        with open(PERSONA_OVERRIDE_PATH, "rb") as f:
            return dict(tomllib.load(f))
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _write_persona_override(data: dict[str, Any]) -> None:
    """将人格覆盖写入 data/persona_override.toml.

    data 需包含 name / prompt / relation (嵌套 dict, 含 persona_addressing /
    user_addressing / context). 全量写入, 调用方应保证包含所有字段.

    转义策略:
    - name / relation 三字段用 basic string (双引号), 反斜杠与双引号需转义
    - prompt 用三引号多行字符串, 内容中包含 ``\"\"\"`` 时替换为 ``\\\"\\\"\\\"``
    """
    def _escape_basic(s: str) -> str:
        # TOML basic string: 反斜杠 + 双引号需转义, 其他 unicode 直接落盘
        return s.replace("\\", "\\\\").replace('"', '\\"')

    def _escape_triple(s: str) -> str:
        # 对于 TOML multi-line basic string ("""):
        # 1. 反斜杠需要转义为 \\ (因为它是转义字符)
        # 2. 连续三个双引号需要用 \""" 避免结束字符串
        s = s.replace("\\", "\\\\")
        s = s.replace('"""', '\\"""')
        return s

    PERSONA_OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    name = str(data.get("name", ""))
    prompt = (str(data.get("prompt") or "")).strip()
    rel = data.get("relation", {}) or {}
    persona_addr = str(rel.get("persona_addressing", ""))
    user_addr = str(rel.get("user_addressing", ""))
    ctx = str(rel.get("context", ""))

    lines = [
        "# Mnemosync 人格 override (由面板 PUT /panel/admin/persona 写入)",
        "# 优先级: 本文件 > config.local.toml [persona] > 资源默认值",
        "# 面板 '重置为默认' 操作会删除本文件, 回退到上一级",
        "",
        f'name = "{_escape_basic(name)}"',
        "",
        'prompt = """',
        _escape_triple(prompt),
        '"""',
        "",
        "[relation]",
        f'persona_addressing = "{_escape_basic(persona_addr)}"',
        f'user_addressing = "{_escape_basic(user_addr)}"',
        f'context = "{_escape_basic(ctx)}"',
        "",
    ]
    PERSONA_OVERRIDE_PATH.write_text("\n".join(lines), encoding="utf-8")


def _delete_persona_override() -> bool:
    """删除 persona override 文件. 返回 True 表示已删除, False 表示不存在."""
    if PERSONA_OVERRIDE_PATH.exists():
        PERSONA_OVERRIDE_PATH.unlink()
        return True
    return False


def load_settings() -> Settings:
    """加载配置.

    优先级: data/persona_override.toml > config.local.toml > 资源默认值.
    """
    if not LOCAL_CONFIG_PATH.exists():
        # 无本地配置也允许启动 (v0.2.3 起模型绑定不再依赖配置文件)
        settings = Settings()
        raw_persona: dict[str, Any] = {}
    else:
        with open(LOCAL_CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)
        settings = Settings(
            storage=StorageConfig(**data.get("storage", {})),
            memory=MemoryConfig(**data.get("memory", {})),
            graph=GraphConfig(**data.get("graph", {})),
            runtime=RuntimeConfig(**data.get("runtime", {})),
        )
        raw_persona = dict(data.get("persona", {}))

    # 人格构建: 资源 TOML → config.local.toml [persona] → persona_override.toml
    override = _load_persona_override()
    if override:
        raw_rel = dict(raw_persona.get("relation", {}))
        override_rel = override.get("relation", {})
        merged = {**raw_persona, **override}
        if override_rel:
            merged_rel = {**raw_rel, **override_rel}
            merged["relation"] = merged_rel
        raw_persona = merged

    persona = _build_persona_config(raw_persona)
    return Settings(
        persona=persona,
        storage=settings.storage,
        memory=settings.memory,
        graph=settings.graph,
        runtime=settings.runtime,
    )


# 全局单例（首次访问时加载）
_settings: Settings | None = None


def _build_persona_config(raw: dict[str, Any]) -> PersonaConfig:
    """从 config.local.toml 的 [persona] 段构造 PersonaConfig.

    行为:
    - `[persona]` 段整体缺失 → 全部走资源 TOML 默认值 (由 PersonaConfig 的 default_factory 负责)
    - 用户显式覆盖 name/prompt 时使用用户值
    - `[persona.relation]` 支持部分覆盖 (只写 user_addressing 时, 其他字段继承资源 TOML 默认)
    """
    defaults = _load_default_persona()
    name = str(raw.get("name") or defaults["name"])
    prompt = str(raw.get("prompt") or defaults["prompt"])
    raw_rel = raw.get("relation") or {}
    default_rel = defaults["relation"]
    relation = RelationConfig(
        persona_addressing=str(raw_rel.get("persona_addressing") or default_rel["persona_addressing"]),
        user_addressing=str(raw_rel.get("user_addressing") or default_rel["user_addressing"]),
        context=str(raw_rel.get("context") or default_rel["context"]),
    )
    return PersonaConfig(name=name, prompt=prompt, relation=relation)


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
