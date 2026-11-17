# -*- coding: utf-8 -*-
"""
agent_cli.py —— 用 WorkBuddy CLI 做分析（主程序调 agent）

为什么用 CLI 而不是 HTTP:
  WorkBuddy 没有对外暴露的推理 HTTP 接口，但自带 codebuddy CLI（node 写的单文件入口），
  `-p/--print` 是文档化的非交互模式，`--output-format json` 能拿到结构化结果。
  这条路等价于"主程序起一个 agent 会话"，且能吃本机的模型注册表。

三个必须处理的坑（均实测）:
  1. 默认端口 58996 被桌面端占用 -> EADDRINUSE。每次调用自己挑一个空闲端口。
  2. 系统代理环境变量（http_proxy=127.0.0.1:59432）会让 CLI 的 setProxy 抛
     "Right-hand side of 'instanceof' is not callable"，整个会话无输出。
     -> 调用时把 *_proxy 全部剥掉，并把本地地址写进 NO_PROXY。
  3. 云端模型（hy4flash 等）需要登录态（Authentication required）；本机注册的
     `custom-local:qwen3.8-27b` 不需要。默认走后者，失败再回退。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------- 定位 CLI ----------

_CLI_CANDIDATES = [
    r"E:/software/workbuddy/resources/app.asar.unpacked/cli/bin/codebuddy",
    r"C:/Program Files/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy",
    r"C:/Program Files (x86)/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy",
]

_NODE_CANDIDATES = [
    r"C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-6/node.exe",
]

# 默认模型：先用本机注册的本地模型（免登录），云端模型作为可选项
DEFAULT_MODEL = os.environ.get("WORKBUDDY_MODEL", "custom-local:qwen3.8-27b")
FALLBACK_MODEL = "custom-local:qwen3.8-27b"


def _find_file(cands: List[str], extra: Optional[List[str]] = None) -> str:
    for p in (extra or []) + cands:
        if p and Path(p).exists():
            return p
    return ""


def find_cli() -> Tuple[str, str]:
    """返回 (node 可执行, codebuddy 入口脚本)。找不到时返回空串。"""
    env_cli = os.environ.get("WORKBUDDY_CLI", "")
    env_node = os.environ.get("WORKBUDDY_NODE", "")
    cli = _find_file(_CLI_CANDIDATES, [env_cli] if env_cli else None)
    if not cli:
        # 允许 PATH 里直接有 codebuddy
        from shutil import which
        w = which("codebuddy") or which("codebuddy.cmd")
        if w:
            return "", w
    if not cli:
        return "", ""
    node = _find_file(_NODE_CANDIDATES, [env_node] if env_node else None)
    if not node:
        from shutil import which
        node = which("node") or ""
    if not node:
        # Windows 下 shutil.which 可能拿到无扩展名的 Unix 脚本，退回 node.exe
        for c in _NODE_CANDIDATES:
            if Path(c).exists():
                node = c
                break
    return node, cli


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _clean_env(cwd: str) -> Dict[str, str]:
    """剥掉代理变量：否则 CLI 的 setProxy 会直接抛错。"""
    env = dict(os.environ)
    for k in list(env.keys()):
        if k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
            env.pop(k, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    env.setdefault("CODEBUDDY_PROJECT_DIR", cwd)
    env.setdefault("CLAUDE_PROJECT_DIR", cwd)
    # 桌面端注入的 MCP 配置会拖慢启动，headless 分析用不到
    env.pop("CODEBUDDY_MCP_CONFIG", None)
    return env


# ---------- 结果解析 ----------

def _extract_text(payload: Any) -> str:
    """从 --output-format json 的输出里取助手正文。

    实测结构是一个消息数组，助手消息形如:
      {"role": "assistant", "content": [{"type": "output_text", "text": "..."}]}
    会话早期还会夹带 user 回显和工具消息，只取 assistant 的 output_text。
    """
    msgs = payload if isinstance(payload, list) else [payload]
    chunks: List[str] = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        if m.get("role") not in (None, "assistant"):
            continue
        content = m.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") in ("output_text", "text") and b.get("text"):
                    chunks.append(str(b["text"]))
                elif b.get("type") == "result" and b.get("result"):
                    chunks.append(str(b["result"]))
        result = m.get("result")
        if isinstance(result, str) and result.strip():
            chunks.append(result)
    return "\n".join(c for c in chunks if c and c.strip()).strip()


def _classify_error(out: str, err: str) -> str:
    blob = f"{out}\n{err}"
    if "Authentication required" in blob or "Please use /login" in blob:
        return "auth"
    if "EADDRINUSE" in blob:
        return "port"
    if "fetch config failed" in blob:
        return "proxy"
    return "unknown"


def call(
    prompt: str,
    model: str = "",
    timeout: float = 180.0,
    cwd: Optional[str] = None,
    retry_port: bool = True,
) -> Dict[str, Any]:
    """起一个 headless agent 会话，返回 {ok, text, raw, error, error_kind, model, ms}。"""
    t0 = time.time()
    node, cli = find_cli()
    if not cli:
        return {"ok": False, "text": "", "error": "未找到 WorkBuddy CLI（可设置环境变量 WORKBUDDY_CLI）",
                "error_kind": "not_found", "model": model or DEFAULT_MODEL, "ms": 0}
    cwd = cwd or os.getcwd()
    model = model or DEFAULT_MODEL
    cmd = ([node] if node else []) + [
        cli, "--port", str(_free_port()),
        "--output-format", "json",
        "--model", model,
        "-p", prompt,
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, env=_clean_env(cwd),
            capture_output=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "text": "", "error": f"agent 超时（>{int(timeout)}s）",
                "error_kind": "timeout", "model": model, "ms": int((time.time() - t0) * 1000)}
    out = proc.stdout or ""
    err = proc.stderr or ""
    kind = _classify_error(out, err)

    if kind == "port" and retry_port:
        return call(prompt, model, timeout, cwd, retry_port=False)

    payload = None
    if out.strip():
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            # 可能混了日志行，尝试截取首个 { 或 [ 到末尾
            for start in (i for i, ch in enumerate(out) if ch in "[{"):
                try:
                    payload = json.loads(out[start:])
                    break
                except json.JSONDecodeError:
                    continue
    text = _extract_text(payload) if payload is not None else out.strip()

    if text:
        return {"ok": True, "text": text, "raw": payload, "error": "",
                "error_kind": "", "model": model, "ms": int((time.time() - t0) * 1000)}

    hint = {
        "auth": "WorkBuddy CLI 未登录：请在终端执行 `codebuddy` 后输入 /login，或在配置里改用本地模型",
        "proxy": "CLI 受系统代理干扰（已尝试清空代理变量），请检查 http_proxy 设置",
        "port": "端口冲突，已重试仍失败",
        "unknown": (err or out or "agent 无输出").strip().splitlines()[0][:200] if (err or out) else "agent 无输出",
    }.get(kind, "agent 无输出")
    return {"ok": False, "text": "", "raw": payload, "error": hint,
            "error_kind": kind, "model": model, "ms": int((time.time() - t0) * 1000),
            "stderr_tail": err[-600:], "stdout_tail": out[-600:]}


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """从模型回复里抠出 JSON 对象（容忍 ```json 包裹和前后废话）。"""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        s = s.replace("```json", "").replace("```", "").strip()
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        pass
    start = s.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        v = json.loads(s[start:i + 1])
                        if isinstance(v, dict):
                            return v
                    except json.JSONDecodeError:
                        break
        start = s.find("{", start + 1)
    return None


def status(probe: bool = False) -> Dict[str, Any]:
    """CLI 可用性。probe=True 时真跑一次超短 prompt（约 10-30s）。"""
    node, cli = find_cli()
    info = {
        "available": bool(cli),
        "cli": cli,
        "node": node,
        "model": DEFAULT_MODEL,
        "fallback_model": FALLBACK_MODEL,
    }
    if not cli:
        info["hint"] = "未找到 WorkBuddy CLI，可通过环境变量 WORKBUDDY_CLI 指定入口脚本"
        return info
    if probe:
        r = call("只回答两个字：收到", timeout=60)
        info.update({"ok": r["ok"], "ms": r["ms"], "error": r.get("error", ""),
                     "error_kind": r.get("error_kind", ""), "reply": r.get("text", "")[:40]})
    return info


if __name__ == "__main__":
    print(json.dumps(status(probe="--probe" in sys.argv), ensure_ascii=False, indent=2))
