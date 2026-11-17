# -*- coding: utf-8 -*-
"""创作工作台模块。

分四层，彼此不反向依赖：
    config   —— 路径 / ComfyUI 连接参数 / 环境自检
    planner  —— 用本地 LLM 把「爆款」或「一句话」变成可执行的创作方案
    comfy    —— MiniMax H3 出片客户端（提交 / 轮询 / 取产物）
    pipeline —— 方案落盘 + 出片任务编排（后台线程、进度、拼接成片）
"""
