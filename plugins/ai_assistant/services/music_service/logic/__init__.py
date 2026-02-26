"""
ai_assistant.music_service.logic

该包用于存放“点歌业务逻辑/策略层”代码（LLM 解析、联网检索策略、快路径判定、相关性评分等），
避免把所有逻辑都堆在 runtime.py 中。

注意：这里的模块应尽量避免 import runtime.MusicService 以免循环依赖；
通过 Mixin 的方式为 MusicService 提供方法实现。
"""
