---
version: 1
placeholders: [SYSTEM_MESSAGE]
---
请分析以下客户端 system 消息, 分离人格描述和功能性指令:

=== 客户端 system 消息 ===
__SYSTEM_MESSAGE__
=== 结束 ===

请逐句调用 classify_sentence_type 工具, 然后输出最终 JSON。