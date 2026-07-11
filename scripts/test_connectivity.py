"""连通性测试：验证 config.local.toml 中配置的 chat / embedding / rerank 服务可用.

用法:
    uv run python scripts/test_connectivity.py

测试内容:
    1. chat 端点：基础对话（验证模型名 + api_key）
    2. chat 端点：function_call（验证 ReAct 可行性）
    3. embedding 端点：文本向量 + 维度
    4. rerank 端点：候选精排

读取 config.local.toml（已被 gitignore），不硬编码密钥.
"""

import sys
import tomllib
from pathlib import Path

import httpx

CONFIG_PATH = Path(__file__).parent.parent / "config.local.toml"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"✗ 找不到 {CONFIG_PATH}")
        print("  请先复制模板: cp config.example.toml config.local.toml")
        sys.exit(1)
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def test_chat_basic(cfg: dict) -> bool:
    chat = cfg["chat"]
    print("\n[1/4] 测试 chat 基础对话...")
    print(f"      base_url: {chat['base_url']}")
    print(f"      model:    {chat['main_model']}")

    payload = {
        "model": chat["main_model"],
        "messages": [
            {"role": "system", "content": "你是测试助手，只回复 OK"},
            {"role": "user", "content": "请回复 OK"},
        ],
        "max_tokens": 10,
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {chat['api_key']}",
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.post(
            f"{chat['base_url']}/chat/completions",
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        print(f"  ✓ 成功")
        print(f"    回复: {content!r}")
        print(f"    tokens: prompt={usage.get('prompt_tokens')}, completion={usage.get('completion_tokens')}")
        return True
    except httpx.HTTPStatusError as e:
        print(f"  ✗ HTTP {e.response.status_code}: {e.response.text[:200]}")
        return False
    except Exception as e:
        print(f"  ✗ 异常: {e}")
        return False


def test_chat_function_call(cfg: dict) -> bool:
    chat = cfg["chat"]
    model = chat["assist_model"]
    print(f"\n[2/4] 测试 chat function_call（assist_model，ReAct 可行性）...")
    print(f"      model: {model}")

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
        "messages": [{"role": "user", "content": "北京天气怎么样？请用 get_weather 工具查询"}],
        "tools": tools,
        "tool_choice": "auto",
        "max_tokens": 200,
    }
    headers = {
        "Authorization": f"Bearer {chat['api_key']}",
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.post(
            f"{chat['base_url']}/chat/completions",
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        finish_reason = data["choices"][0].get("finish_reason")
        tool_calls = msg.get("tool_calls")

        if tool_calls:
            print(f"  ✓ 模型发起了 function_call")
            for tc in tool_calls:
                fn = tc["function"]
                print(f"    → {fn['name']}({fn['arguments']})")
            print(f"    finish_reason: {finish_reason}")
            return True
        else:
            print(f"  ⚠ 模型未调用工具，直接回复: {msg.get('content', '')[:100]!r}")
            print(f"    finish_reason: {finish_reason}")
            print(f"    （function_call 能力可能不足，需换辅助模型）")
            return False
    except httpx.HTTPStatusError as e:
        print(f"  ✗ HTTP {e.response.status_code}: {e.response.text[:200]}")
        return False
    except Exception as e:
        print(f"  ✗ 异常: {e}")
        return False


def test_embedding(cfg: dict) -> bool:
    emb = cfg["embedding"]
    print("\n[3/4] 测试 embedding...")
    print(f"      base_url: {emb['base_url']}")
    print(f"      model:    {emb['model']}")

    payload: dict = {
        "model": emb["model"],
        "input": "用户对花生过敏",
    }
    if "dimensions" in emb and emb["dimensions"]:
        payload["dimensions"] = emb["dimensions"]

    headers = {
        "Authorization": f"Bearer {emb['api_key']}",
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.post(
            f"{emb['base_url']}/embeddings",
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        vec = data["data"][0]["embedding"]
        print(f"  ✓ 成功")
        print(f"    向量维度: {len(vec)}")
        print(f"    前 5 维: {vec[:5]}")
        # 校验维度与配置一致
        if "dimensions" in emb and emb["dimensions"]:
            if len(vec) != emb["dimensions"]:
                print(f"  ⚠ 配置 dimensions={emb['dimensions']}，实际={len(vec)}（不一致）")
        return True
    except httpx.HTTPStatusError as e:
        print(f"  ✗ HTTP {e.response.status_code}: {e.response.text[:200]}")
        return False
    except Exception as e:
        print(f"  ✗ 异常: {e}")
        return False


def test_rerank(cfg: dict) -> bool:
    if "rerank" not in cfg:
        print("\n[4/4] 跳过 rerank（未配置）")
        return True
    rr = cfg["rerank"]
    print(f"\n[4/4] 测试 rerank...")
    print(f"      base_url: {rr['base_url']}")
    print(f"      model:    {rr['model']}")

    payload = {
        "model": rr["model"],
        "query": "我对花生过敏",
        "documents": [
            "用户对海鲜过敏",
            "我喜欢吃花生酱",
            "今天天气真好",
        ],
        "top_n": 2,
    }
    headers = {
        "Authorization": f"Bearer {rr['api_key']}",
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.post(
            f"{rr['base_url']}/rerank",
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        print(f"  ✓ 成功")
        print(f"    返回 {len(results)} 条:")
        for r in results:
            idx = r.get("index")
            score = r.get("relevance_score")
            doc = r.get("document", {})
            doc_text = doc.get("text", "") if isinstance(doc, dict) else str(doc)
            print(f"      [{idx}] score={score:.4f}  {doc_text[:40]}")
        return True
    except httpx.HTTPStatusError as e:
        print(f"  ✗ HTTP {e.response.status_code}: {e.response.text[:200]}")
        # 尝试备用端点
        if e.response.status_code == 404:
            print(f"  → /rerank 返回 404，尝试 /reranks ...")
            try:
                resp2 = httpx.post(
                    f"{rr['base_url']}/reranks",
                    json=payload,
                    headers=headers,
                    timeout=30,
                )
                resp2.raise_for_status()
                data = resp2.json()
                results = data.get("results", [])
                print(f"  ✓ /reranks 成功，返回 {len(results)} 条:")
                for r in results:
                    idx = r.get("index")
                    score = r.get("relevance_score")
                    print(f"      [{idx}] score={score:.4f}")
                return True
            except Exception as e2:
                print(f"  ✗ /reranks 也失败: {e2}")
        return False
    except Exception as e:
        print(f"  ✗ 异常: {e}")
        return False


def main():
    print("=" * 60)
    print("Mnemosync 连通性测试")
    print("=" * 60)

    cfg = load_config()

    results = {
        "chat 基础": test_chat_basic(cfg),
        "chat function_call": test_chat_function_call(cfg),
        "embedding": test_embedding(cfg),
        "rerank": test_rerank(cfg),
    }

    print("\n" + "=" * 60)
    print("测试汇总")
    print("=" * 60)
    for name, ok in results.items():
        mark = "✓" if ok else "✗"
        print(f"  {mark} {name}")

    all_ok = all(results.values())
    if all_ok:
        print("\n🎉 全部通过，可继续开发。")
    else:
        print("\n⚠ 有失败项，需调整配置后重试。")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
