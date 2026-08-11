# -*- coding: utf-8 -*-
"""元宝本地代理：在用户本机运行，前端自动发现后通过它完成元宝登录/改写。

启动：python station/yuanbao_local_proxy.py
前端检测：fetch('http://localhost:9224/health')
"""

import asyncio
import json
import logging
import sys
from aiohttp import web
from pathlib import Path

_HERE = Path(__file__).resolve().parent
SERVER_DIR = _HERE / "server"
sys.path.insert(0, str(SERVER_DIR))

import yuanbao_client as YB

logger = logging.getLogger("yb-proxy")
logging.basicConfig(level=logging.INFO, format="[yb-proxy] %(message)s")

routes = web.RouteTableDef()


@routes.get("/health")
async def health(_req):
    return web.json_response({"ok": True, "has_profile": YB.has_profile()})


@routes.post("/rewrite")
async def rewrite(req: web.Request):
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    frames = body.get("frames", [])
    raw_text = body.get("raw_text", "")
    template = body.get("rewrite_template")
    max_chars = body.get("max_chars")
    topic = body.get("topic", "")
    timeout = body.get("timeout", 120)

    result = YB.vision_and_rewrite(
        frames, raw_text,
        rewrite_template=template,
        max_chars=max_chars,
        topic=topic,
        timeout=timeout,
        headless=False,  # 用户本机有桌面，用有头模式
    )
    return web.json_response(result)


@routes.post("/login")
async def login(_req: web.Request):
    ok = YB.login()
    return web.json_response({"ok": ok})


@routes.get("/status")
async def status(_req: web.Request):
    return web.json_response({
        "has_profile": YB.has_profile(),
        "channel": YB._pick_channel(),
    })


def main():
    port = 9224
    app = web.Application()
    app.add_routes(routes)
    logger.info(f"元宝本地代理启动 → http://localhost:{port}")
    logger.info("  前端访问 vu.evenblue.top 时自动检测此代理")
    logger.info("  端点: /health /login /rewrite /status")
    web.run_app(app, host="127.0.0.1", port=port, print=None)


if __name__ == "__main__":
    main()
