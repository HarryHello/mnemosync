"""探测硅基流动上哪些模型支持 function_call.

逐一测试候选模型，找出能正确发起 tool_call 的模型.
"""

import tomllib
from pathlib import Path

import httpx

CONFIG_PATH = Path(__file__).parent.parent / "config.local.toml"


def load_chat_cfg() -> dict:
    with open(CONFIG_PATH, "rb") as f:
        cfg = tomllib.load(f)
    return cfg["chat"]


def test_model_function_call(base_url: str, api_key: str, model: str) -> str:
    """测试单个模型的 function_call 能力.

    Returns:
        'ok'         成功发起 tool_call
        'no_call'    未调用工具，输出文本
        'not_found'  模型不存在
        'error'      其他错误
    """
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "查询指定城市的天气",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "城市名"},
                    },
                    "required": ["city"],
                },
            },
        }
    ]
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "北京天气怎么样？请必须使用 get_weather 工具查询"}],
        "tools": tools,
        "tool_choice": "auto",
        "max_tokens": 200,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = httpx.post(
            f"{base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=30,
        )
        if resp.status_code == 404:
            return "not_found"
        if resp.status_code != 200:
            return f"error_{resp.status_code}"
        data = resp.json()
        msg = data["choices"][0]["message"]
        if msg.get("tool_calls"):
            return "ok"
        return "no_call"
    except Exception as e:
        return f"error_{type(e).__name__}"


def main():
    chat = load_chat_cfg()
    base_url = chat["base_url"]
    api_key = chat["api_key"]

    # 候选模型（硅基流动上可能支持 function_call 的）
    candidates = [
        "Qwen/Qwen2.5-7B-Instruct",
        "Qwen/Qwen2.5-72B-Instruct",
        "Qwen/Qwen3-8B",
        "Qwen/Qwen3-32B",
        "Qwen/Qwen3-30B-A3B-Instruct",
        "Qwen/Qwen2.5-Coder-7B-Instruct",
        "THUDM/glm-4-9b-chat",
        "meta-llama/Meta-Llama-3.1-8B-Instruct",
    ]

    print("=" * 64)
    print("探测硅基流动模型 function_call 支持")
    print("=" * 64)
    print(f"{'模型':<42} {'结果':<20}")
    print("-" * 64)

    ok_models = []
    for model in candidates:
        result = test_model_function_call(base_url, api_key, model)
        mark = "✓" if result == "ok" else ("?" if result == "no_call" else "✗")
        print(f"{model:<42} {mark} {result}")
        if result == "ok":
            ok_models.append(model)

    print("-" * 64)
    if ok_models:
        print(f"\n支持 function_call 的模型（{len(ok_models)} 个）:")
        for m in ok_models:
            print(f"  ✓ {m}")
        print(f"\n建议辅助模型用: {ok_models[0]}")
    else:
        print("\n✗ 没有找到支持 function_call 的模型")


if __name__ == "__main__":
    main()
