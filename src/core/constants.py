"""全局常量."""

# 虚拟模型 ID: 表示"由 role_bindings 决定主对话模型", 而非具体模型名
# 客户端请求此 ID 时, Mnemosync 根据当前绑定解析为实际模型
VIRTUAL_MODEL_ANY = "mnemosync-any"

# 默认/单人格标识 (v0.3.x 单人格阶段, 未来多人格时从配置派生)
DEFAULT_PERSONA_ID = "default"
