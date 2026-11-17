# -*- coding: utf-8 -*-
"""_cli.py —— 复用 hot_capture/agent_cli 的 WorkBuddy CLI 调用。

hot_capture 是散装模块（靠 sys.path 注入），creator 是正规包，
这里做一层薄适配，避免在两个地方各写一遍"剥代理 + 随机端口 + 解析 JSON"的坑。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

_HOT = Path(__file__).resolve().parent.parent / "hot_capture"
if str(_HOT) not in sys.path:
    sys.path.insert(0, str(_HOT))

import agent_cli  # noqa: E402


def agent_call(prompt: str, timeout: float = 300.0, model: str = "") -> Dict[str, Any]:
    return agent_cli.call(prompt, model=model or agent_cli.DEFAULT_MODEL, timeout=timeout)


def agent_extract_json(text: str) -> Optional[Dict[str, Any]]:
    return agent_cli.extract_json(text)


def agent_status(probe: bool = False) -> Dict[str, Any]:
    return agent_cli.status(probe=probe)
