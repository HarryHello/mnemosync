"""OpenAI 兼容的转发 API 路由."""

import uuid
import time
from fastapi import APIRouter, HTTPException
from src.api.schemas.forward import (
    ModelList,
    ModelInfo,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionChoice,
    ChatMessage,
    UsageInfo,
)

# OpenAI 兼容的路由，使用 /v1 前缀
router = APIRouter(prefix="/v1")


@router.get("/models", response_model=ModelList, tags=["Models"])
async def list_models():
    """
    列出可用模型。
    
    目前返回一个占位模型 `mnemosync-any`，表示 Mnemosync 会自动选择合适的模型。
    """
    return ModelList(
        object="list",
        data=[
            ModelInfo(
                id="mnemosync-any",
                object="model",
                created=1686935002,
                owned_by="mnemosync"
            )
        ]
    )


@router.get("/models/{model_id}", response_model=ModelInfo, tags=["Models"])
async def get_model(model_id: str):
    """
    获取特定模型信息。
    """
    if model_id == "mnemosync-any":
        return ModelInfo(
            id="mnemosync-any",
            object="model",
            created=1686935002,
            owned_by="mnemosync"
        )
    raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")


@router.post("/chat/completions", response_model=ChatCompletionResponse, tags=["Chat Completions"])
async def create_chat_completion(request: ChatCompletionRequest):
    """
    创建聊天补全。
    
    接受与 OpenAI API 相同格式的请求，返回模型回应。
    
    ## 处理流程（TODO）:
    1. 验证 API Key
    2. 根据请求中的模型或用户配置选择合适的后端模型
    3. 对消息进行预处理（清洗、增强等）
    4. 调用后端 LLM 服务
    5. 对响应进行后处理
    6. 返回结果
    
    ## 当前状态:
    目前返回一个占位响应，用于测试接口连通性。
    """
    # TODO: 实现完整的聊天补全逻辑
    
    # 生成响应 ID
    response_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    
    # 创建占位响应
    return ChatCompletionResponse(
        id=response_id,
        object="chat.completion",
        created=int(time.time()),
        model=request.model,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content=(
                        "🔧 Mnemosync 占位响应\n\n"
                        "聊天补全功能正在开发中。当前已接收到的请求:\n\n"
                        f"- **Model**: `{request.model}`\n"
                        f"- **Messages**: {len(request.messages)} 条\n"
                        f"- **Temperature**: {request.temperature}\n"
                        f"- **Max Tokens**: {request.max_tokens}\n\n"
                        "请稍后再来查看完整实现！"
                    )
                ),
                finish_reason="stop"
            )
        ],
        usage=UsageInfo(
            prompt_tokens=sum(len(msg.content or "") for msg in request.messages) // 4,
            completion_tokens=50,
            total_tokens=sum(len(msg.content or "") for msg in request.messages) // 4 + 50
        )
    )
