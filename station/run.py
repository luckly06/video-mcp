# -*- coding: utf-8 -*-
"""
run.py — 一键启动 MCP Server + 打开 Agent 工位网页

流程：
  1. 启动 server/mcp_server.py（HTTP 服务在 127.0.0.1:8765，只服务 /mcp）
  2. 等 server 就绪（TCP 端口可连）
  3. 在系统默认浏览器打开 web/index.html（Web 工位壳，直接 file:// 打开即可）
  4. Ctrl+C 时优雅关掉 server

依赖：Python 3.x 标准库；工程 vendor/ffmpeg/ 里已同捆 ffmpeg。无需 pip install。
"""

import os
import sys
import time
import socket
import signal
import subprocess
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "server" / "mcp_server.py"
WEB_INDEX = ROOT / "web" / "index.html"
HOST = "127.0.0.1"
PORT = 8765


def wait_port(host: str, port: int, timeout: float = 8.0) -> bool:
    """轮询端口，直到 server 起来或超时。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            try:
                s.connect((host, port))
                return True
            except OSError:
                time.sleep(0.2)
    return False


def main() -> int:
    if not SERVER.exists():
        print(f"[run.py] server 脚本不存在: {SERVER}", file=sys.stderr)
        return 1
    if not WEB_INDEX.exists():
        print(f"[run.py] web 入口不存在: {WEB_INDEX}", file=sys.stderr)
        return 1

    print(f"[run.py] 启动 MCP Server → http://{HOST}:{PORT}/mcp")
    # 用当前解释器起子进程，保证虚拟环境一致
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        cwd=str(ROOT),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    try:
        if not wait_port(HOST, PORT):
            print("[run.py] server 未在 8s 内就绪，放弃", file=sys.stderr)
            proc.terminate()
            return 2

        print(f"[run.py] server 就绪，打开工位网页: {WEB_INDEX}")
        webbrowser.open(WEB_INDEX.as_uri())

        print("[run.py] 按 Ctrl+C 停止 server")
        proc.wait()
    except KeyboardInterrupt:
        print("\n[run.py] 收到中断，关闭 server ...")
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    return proc.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
