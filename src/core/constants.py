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

# 工具调用事件的固定 token 估算值
TOOL_CALL_TOKEN_COUNT: int = 8

# 虚拟模型的固定创建时间戳 (2023-06-16)
VIRTUAL_MODEL_CREATED_AT: int = 1686935002

# 辅助 Agent ReAct 最大迭代次数 (与 AgentSpec 保持一致)
MEMORY_ANALYSIS_MAX_ITERATIONS: int = 4
RELATIONSHIP_ANALYSIS_MAX_ITERATIONS: int = 2
