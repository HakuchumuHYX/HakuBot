import json
import asyncio
import httpx
import re

# 1. 读取配置
try:
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
except Exception as e:
    print(f"❌ 无法读取 config.json: {e}")
    exit(1)

API_KEY = config.get("api_key")
BASE_URL = config.get("base_url")
IMAGE_MODEL = config.get("image_model")
TIMEOUT = config.get("timeout", 60)
PROXY = config.get("proxy")

print(f"⚙️  配置加载:")
print(f"   Model: {IMAGE_MODEL}")
print(f"   Base URL: {BASE_URL}")
print("-" * 40)


async def test_image_generation_fix():
    # === 关键点 1: 构造强效 Prompt ===
    system_prompt = (
        "You are an image generation tool. "
        "Do NOT write python code, do NOT explain. "
        "Directly generate the image requested by the user. "
        "Output ONLY the image URL in Markdown format: ![image](url)."
    )
    user_prompt = "Generate an image of a cute cat"

    payload = {
        "model": IMAGE_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    # 自动处理 endpoint 拼接
    url = f"{BASE_URL.rstrip('/')}/chat/completions"
    if "/v1" not in url and "chat/completions" not in url:
        url = f"{BASE_URL.rstrip('/')}/v1/chat/completions"
    if "chat/completions" in BASE_URL:
        url = BASE_URL

    print(f"🚀 发送请求 (伪装 Chat + 强 Prompt)...")
    print(f"📦 System: {system_prompt}")
    print(f"📦 User: {user_prompt}")

    async with httpx.AsyncClient(proxies=PROXY, timeout=TIMEOUT) as client:
        try:
            resp = await client.post(url, json=payload, headers=headers)

            print("\n" + "=" * 20 + " 服务器响应 " + "=" * 20)
            print(f"Status: {resp.status_code}")

            if resp.status_code != 200:
                print(f"❌ 报错: {resp.text}")
                return

            data = resp.json()
            # 打印原始 JSON (方便调试)
            print(json.dumps(data, indent=2, ensure_ascii=False))

            # === 关键点 2: 尝试提取结果 ===
            print("\n🧐 结果分析:")
            try:
                content = data["choices"][0]["message"]["content"]
                if not content:
                    print("❌ Content 为空！可能被安全拦截。")
                    return

                print(f"📝 原始回复文本:\n{content}\n")

                # 正则提取
                match = re.search(r'\!\[.*?\]\((.*?)\)', content)
                if match:
                    print(f"✅ 成功提取到 Markdown 图片链接: {match.group(1)}")
                else:
                    # 尝试找纯 URL
                    urls = re.findall(r'(https?://[^\s)"]+)', content)
                    valid_urls = [u for u in urls if not u.endswith(('.py', '.html', '.js'))]

                    if valid_urls:
                        print(f"✅ 提取到疑似图片链接: {valid_urls[0]}")
                    else:
                        print("❌ 未找到图片链接，模型可能依然在输出文本/代码。")

            except Exception as e:
                print(f"❌ 解析异常: {e}")

        except Exception as e:
            print(f"❌ 请求发生异常: {e}")


if __name__ == "__main__":
    asyncio.run(test_image_generation_fix())