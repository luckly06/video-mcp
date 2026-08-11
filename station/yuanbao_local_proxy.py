# -*- coding: utf-8 -*-
"""元宝本地代理：在用户本机运行，前端自动发现后通过它完成元宝登录/改写。

启动：python station/yuanbao_local_proxy.py
前端检测：fetch('http://localhost:9224/health')
"""

import json
import sys
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

_HERE = Path(__file__).resolve().parent
SERVER_DIR = _HERE / "server"
sys.path.insert(0, str(SERVER_DIR))

import yuanbao_client as YB


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[yb-proxy] {args[0]}" if args else f"[yb-proxy] {fmt}")

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._json(200, {})

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True, "has_profile": YB.has_profile()})
        elif self.path == "/status":
            self._json(200, {"has_profile": YB.has_profile(), "channel": YB._pick_channel()})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else {}
        except Exception:
            self._json(400, {"error": "invalid json"})
            return

        if self.path == "/login":
            try:
                ok = YB.login()
                self._json(200, {"ok": ok})
            except Exception as e:
                self._json(500, {"error": str(e)})

        elif self.path == "/rewrite":
            try:
                result = YB.vision_and_rewrite(
                    body.get("frames", []),
                    body.get("raw_text", ""),
                    rewrite_template=body.get("rewrite_template"),
                    max_chars=body.get("max_chars"),
                    topic=body.get("topic", ""),
                    timeout=body.get("timeout", 120),
                    headless=False,
                )
                self._json(200, result)
            except Exception as e:
                traceback.print_exc()
                self._json(500, {"error": str(e)})

        else:
            self._json(404, {"error": "not found"})


def main():
    port = 9224
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"[yb-proxy] 元宝本地代理 → http://localhost:{port}")
    print("[yb-proxy] 端点: /health /login /rewrite /status")
    print("[yb-proxy] 前端访问 vu.evenblue.top 时自动检测此代理")
    print("[yb-proxy] Ctrl+C 退出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[yb-proxy] 已退出")
        server.server_close()


if __name__ == "__main__":
    main()
