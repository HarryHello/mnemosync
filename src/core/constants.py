"""全局常量."""

# 虚拟模型 ID: 表示"由 role_bindings 决定主对话模型", 而非具体模型名
# 客户端请求此 ID 时, Mnemosync 根据当前绑定解析为实际模型
VIRTUAL_MODEL_ANY = "mnemosync-any"

# 默认/单人格标识 (v0.3.x 单人格阶段, 未来多人格时从配置派生)
DEFAULT_PERSONA_ID = "default"

# 记忆 ACTIVE 状态的优先级门槛 (priority > threshold → ACTIVE)
MEMORY_ACTIVE_PRIORITY_THRESHOLD: float = 0.3

# 对话存储默认列表查询上限
DEFAULT_LIST_LIMIT: int = 5000

# 审计日志清理周期 (秒): 后台协程每天清理一次过期对话流水
AUDIT_LOG_RETENTION_INTERVAL: int = 24 * 3600

# HTTP 请求/响应日志 body 截断长度 (字符)
LOG_BODY_MAX_CHARS: int = 1000

# Prompt 版本备份文件名冲突重试上限 (生成唯一时间戳文件名)
PROMPT_VERSION_MAX_ATTEMPTS: int = 1000

# 工具调用事件的固定 token 估算值
TOOL_CALL_TOKEN_COUNT: int = 8

# 虚拟模型的固定创建时间戳 (2023-06-16)
VIRTUAL_MODEL_CREATED_AT: int = 1686935002

# 辅助 Agent ReAct 最大迭代次数 (与 AgentSpec 保持一致)
MEMORY_ANALYSIS_MAX_ITERATIONS: int = 4
RELATIONSHIP_ANALYSIS_MAX_ITERATIONS: int = 2

# 版本升级一次性通知 (版本号 → 通知内容)
# 仅在对应版本首次启动时发送一次, 用户阅读后不再重复
UPGRADE_NOTIFICATION_VERSIONS: dict[str, str] = {
    "0.3.5": (
        "v0.3.5 升级了 ChromaDB (0.6→1.5) 和 LangGraph (0.6→1.2)。"
        "如果向量检索出现问题，请执行 `mnemosync memory reindex` 重建向量库。"
    ),
}
