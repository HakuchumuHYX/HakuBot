import json
import sys
from openai import OpenAI


def test_api():
    # 1. 读取配置文件
    config_file = "config.json"
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"❌ 错误：未找到 {config_file} 文件。")
        print("请确保 config.json 和本脚本在同一目录下，并且已填入 API Key。")
        return
    except json.JSONDecodeError:
        print(f"❌ 错误：{config_file} 格式不正确，请检查 JSON 语法。")
        return

    print(f"⚙️  正在读取配置...")
    api_key = config.get("api_key")
    base_url = config.get("base_url")
    model = config.get("model")

    if not api_key:
        print("⚠️  警告：API Key 看起来为空，可能会导致认证失败。")

    print(f"🔄 正在尝试连接 API...")
    print(f"   Base URL: {base_url}")
    print(f"   Target Model: {model}")

    # 2. 初始化客户端
    try:
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=10  # 测试连接设置10秒超时
        )
    except Exception as e:
        print(f"❌ 客户端初始化失败: {e}")
        return

    # 3. 发送简单请求
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": "你好，这是一个连接测试，请回复'连接成功'这四个字。"}
            ]
        )

        # 4. 获取结果
        content = response.choices[0].message.content
        usage = response.usage

        print("\n" + "=" * 30)
        print("✅ 测试成功！API 连接正常")
        print("=" * 30)
        print(f"🤖 AI 回复: {content}")
        print(f"📊 实际调用模型: {response.model}")
        print(
            f"💰 Token 消耗: {usage.total_tokens} (Prompt: {usage.prompt_tokens}, Completion: {usage.completion_tokens})")
        print("=" * 30)

    except Exception as e:
        print("\n" + "=" * 30)
        print("❌ 测试失败：请求发生错误")
        print("=" * 30)
        print(f"错误详情: {e}")
        print("\n常见原因排查：")
        print("1. API Key 是否正确？")
        print("2. Base URL 是否填写正确？(OpenAI官方无需修改，中转需填写完整地址)")
        print("3. 模型名称是否在你的账号权限内？(如 gpt-4 需要特定权限)")
        print("4. 网络是否需要代理？(本脚本未配置代理，如果需要，请在系统环境变量设置 https_proxy)")


if __name__ == "__main__":
    test_api()
    