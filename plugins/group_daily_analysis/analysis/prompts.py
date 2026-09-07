def safe_prompt_format(prompt: str, **kwargs) -> str:
    """
    安全替换 prompt 中的少量占位符。

    说明：
    - 避免使用 str.format()，因为 prompt 里常包含 JSON 示例，带大量 `{}` 会触发 KeyError。
    - 这里只替换我们明确允许的变量：如 {messages_text} / {users_text} / {max_topics} 等。
    """
    for k, v in kwargs.items():
        prompt = prompt.replace("{" + k + "}", str(v))
    return prompt
