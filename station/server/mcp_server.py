# -*- coding: utf-8 -*-
"""
mcp_server.py — 视频去重 MCP Server（遵循 MCP 2026-07-28 无状态规范）

对齐 MCP 2026-07-28 核心变化：
  - 无状态：删除 initialize / notifications/initialized 握手（SEP-2575）
  - 删除 Mcp-Session-Id，任意请求可落在任意实例（SEP-2567）
  - 每个请求在 _meta 携带 protocolVersion / clientInfo
  - Streamable HTTP 要求 Mcp-Method / Mcp-Name 头做路由（SEP-2243）
  - tools/list 响应带 ttlMs / cacheScope，client 可缓存（SEP-2549）
  - server/discover 提供能力发现（替代握手）
  - 长任务用显式 job_id handle 由模型在调用间传递（stateless 应用状态）
  - 人工决策点返回 InputRequiredResult + requestState（SEP-2322 多轮请求）

传输：单文件 stdlib http.server，POST /mcp 收 JSON-RPC。
Hooks 由外层框架（pre_tool_guard / post_tool_audit）在调用链路注入，
本 server 在 tools/call 前后主动调用 hook 脚本，保证"Agent 无法绕开"。
"""

import os
import sys
import json
import uuid
import base64
import socket
import subprocess
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as P  # noqa: E402

PROTOCOL_VERSION = "2026-07-28"
SERVER_INFO = {"name": "video-dedup-station", "version": "1.0.0", "title": "视频去重数字员工"}

_STATION = Path(__file__).resolve().parent.parent
_HOOKS = _STATION / "hooks"
_LOGS = _STATION / "logs"

# 长任务 handle 存储（stateless 协议 + 显式 handle 应用状态）
# key: job_id -> {status, result, ...}。文件持久化，任意进程可读。
_JOBS_FILE = _LOGS / "jobs.json"


# ---------------------------------------------------------------------------
# job handle 持久化（显式 handle 模式，替代协议 session）
# ---------------------------------------------------------------------------
def _load_jobs():
    if _JOBS_FILE.exists():
        try:
            return json.loads(_JOBS_FILE.read_text("utf-8"))
        except Exception:
            return {}
    return {}


def _save_jobs(jobs):
    _LOGS.mkdir(parents=True, exist_ok=True)
    _JOBS_FILE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), "utf-8")


def _new_job(kind, meta):
    jobs = _load_jobs()
    jid = "job_" + uuid.uuid4().hex[:12]
    jobs[jid] = {"job_id": jid, "kind": kind, "status": "completed", "meta": meta}
    _save_jobs(jobs)
    return jid


# ---------------------------------------------------------------------------
# 工具定义（tools/list 返回；带 ttlMs / cacheScope 供缓存）
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "list_assets",
        "description": "列出 video/ 目录下可处理的视频素材（名称/大小）。",
        "inputSchema": {"type": "object", "properties": {}},
        "_tier": "audit",
    },
    {
        "name": "probe_video",
        "description": "读取视频关键信息（分辨率/帧率/编码/时长/MD5）。去重前【必须】先探测。",
        "inputSchema": {
            "type": "object",
            "properties": {"src": {"type": "string", "description": "文件名或绝对路径"}},
            "required": ["src"],
        },
        "_tier": "audit",
    },
    {
        "name": "dedup_video",
        "description": "对单个视频去重（画面微调+微旋转+帧率+降噪+码率），保持分辨率，改变MD5。调用前须先 probe_video。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "src": {"type": "string", "description": "文件名或绝对路径"},
                "params": {"type": "object", "description": "可选：覆盖默认参数（brightness/contrast/saturation/rotate_deg/fps_range/bitrate_mul/bitrate_kbps/denoise）"},
                "out_name": {"type": "string", "description": "可选：输出文件名"},
                "level": {"type": "string", "enum": ["light", "medium", "heavy"], "description": "强度档：light=轻微、medium=中等（默认）、heavy=强烈；控制 crop/speed/trim 幅度"},
                "dimensions": {"type": "object", "description": "维度开关：picture/rotate/crop/flip/speed/trim 六个布尔；flip 默认 false，开了必传 flip_mode"},
                "flip_mode": {"type": "string", "enum": ["h", "v", "90"], "description": "翻转方向：h=水平、v=垂直、90=转置；仅 flip=true 时用"},
                "seed": {"type": "integer", "description": "随机种子；缺省随机回填"},
            },
            "required": ["src"],
        },
        "_tier": "warned",
    },
    {
        "name": "batch_fission",
        "description": "裂变：同一素材生成 count 个不同参数的变体（每个 MD5 互不相同）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "src": {"type": "string"},
                "count": {"type": "integer", "description": "变体数量 1-20"},
                "params": {"type": "object"},
                "level": {"type": "string", "enum": ["light", "medium", "heavy"], "description": "强度档：light=轻微、medium=中等、heavy=强烈；控制 crop/speed/trim 幅度"},
                "dimensions": {"type": "object", "description": "维度开关：picture/rotate/crop/flip/speed/trim 六个布尔；flip 默认 false，开了必传 flip_mode"},
                "flip_mode": {"type": "string", "enum": ["h", "v", "90"], "description": "翻转方向：h=水平、v=垂直、90=转置；仅 flip=true 时用"},
            },
            "required": ["src", "count"],
        },
        "_tier": "warned",
    },
    {
        "name": "list_watermark_templates",
        "description": "列出可用平台水印模板（抖音/腾讯/西瓜等）。",
        "inputSchema": {"type": "object", "properties": {}},
        "_tier": "audit",
    },
    {
        "name": "remove_watermark",
        "description": "按平台模板坐标用 delogo 去除水印。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "src": {"type": "string"},
                "platform": {"type": "string", "description": "平台模板名（见 list_watermark_templates）"},
                "out_name": {"type": "string"},
            },
            "required": ["src", "platform"],
        },
        "_tier": "warned",
    },
    {
        "name": "get_job",
        "description": "按 job_id 查询任务 handle 的状态与结果（显式 handle 模式）。",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
        "_tier": "audit",
    },
    {
        "name": "delete_output",
        "description": "删除 output/ 下的产出文件。【不可逆】，受 hook 硬拦截。",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "output/ 下的文件名"}},
            "required": ["name"],
        },
        "_tier": "blocked",
    },
]

TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


# ---------------------------------------------------------------------------
# Hook 调用（Agent 无法绕开：tools/call 路径独占，hook 是唯一道闸）
# ---------------------------------------------------------------------------
def _run_hook(script, payload):
    """执行 hook 脚本，stdin 传 JSON，stdout 收结构化控制字段。"""
    path = _HOOKS / script
    if not path.exists():
        return {"continue": True}
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        out = proc.stdout.decode("utf-8", "replace").strip()
        return json.loads(out) if out else {"continue": True}
    except Exception as e:
        return {"continue": True, "hook_error": str(e)}


# ---------------------------------------------------------------------------
# 工具执行
# ---------------------------------------------------------------------------
def _exec_tool(name, args):
    if name == "list_assets":
        return {"assets": P.list_assets()}
    if name == "probe_video":
        return P.probe_video(args["src"])
    if name == "dedup_video":
        r = P.dedup_video(
            args["src"],
            params=args.get("params"),
            out_name=args.get("out_name"),
            seed=args.get("seed"),
            level=args.get("level"),
            dimensions=args.get("dimensions"),
            flip_mode=args.get("flip_mode"),
            trim_phase=args.get("trim_phase"),
        )
        r["job_id"] = _new_job("dedup", {"src": r["src"]["name"], "output": r["output_path"]})
        return r
    if name == "batch_fission":
        # count 默认值由 rules.json auto_fill 提供 (5)；不写 fallback 3。
        # 若有人绕过 handle_rpc 直接调 _exec_tool，pipeline 内部 int(count) 会失败，
        # 这是去除 server 双重默认后的明确边界（_exec_tool 是内部接口，不该被直调）。
        # seed / trim_phase 不传：pipeline.batch_fission 签名没有这两个参数。
        r = P.batch_fission(
            args["src"],
            count=args.get("count"),
            params=args.get("params"),
            level=args.get("level"),
            dimensions=args.get("dimensions"),
            flip_mode=args.get("flip_mode"),
        )
        r["job_id"] = _new_job("fission", {"src": r["src"], "count": r["count"]})
        return r
    if name == "list_watermark_templates":
        return {"templates": P.list_watermark_templates()}
    if name == "remove_watermark":
        return P.remove_watermark(args["src"], args["platform"], out_name=args.get("out_name"))
    if name == "get_job":
        jobs = _load_jobs()
        j = jobs.get(args["job_id"])
        if not j:
            raise P.PipelineError(f"job 不存在: {args['job_id']}")
        return j
    if name == "delete_output":
        # 🔒 F3.3：用 _resolve_safe 校验 name 必须落在 OUTPUT_DIR 白名单内，
        # 否则攻击者可用 "../../../etc/passwd" 跨出 OUTPUT_DIR（Windows 上
        # Path / "../.." 经 target.exists()/target.unlink() 会让 OS 解析 ..）。
        # 该工具已被 tier=blocked 拦截，但路径遍历 bug 本身仍存在，
        # 此处做 defense-in-depth。
        target = P._resolve_safe(args["name"], P.OUTPUT_DIR, must_exist=True)
        target.unlink()
        return {"deleted": str(target)}
    raise P.PipelineError(f"未知工具: {name}")


# ---------------------------------------------------------------------------
# JSON-RPC 方法分发（无状态：无 initialize/initialized）
# ---------------------------------------------------------------------------
def handle_rpc(req, headers):
    method = req.get("method")
    rpc_id = req.get("id")
    params = req.get("params", {}) or {}

    # server/discover：替代握手的能力发现
    if method == "server/discover":
        return _ok(rpc_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": SERVER_INFO,
            "capabilities": {"tools": {"listChanged": False}},
        })

    if method == "tools/list":
        public = [{k: v for k, v in t.items() if not k.startswith("_")} for t in TOOLS]
        # SEP-2549：list 结果带缓存元数据
        return _ok(rpc_id, {"tools": public, "ttlMs": 300000, "cacheScope": "shared"})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        tool = TOOLS_BY_NAME.get(name)
        if not tool:
            return _err(rpc_id, -32602, f"未知工具: {name}")

        # --- 人工决策点：SEP-2322 多轮请求 ---
        # 若客户端未带确认，warned/blocked 工具返回 InputRequiredResult
        input_responses = params.get("inputResponses")
        if tool["_tier"] in ("warned", "blocked") and not input_responses:
            state = base64.b64encode(json.dumps({"name": name, "arguments": args}).encode()).decode()
            tier_msg = "⚠️ 高风险操作" if tool["_tier"] == "warned" else "🛑 不可逆删除"
            return _ok(rpc_id, {
                "resultType": "input_required",
                "inputRequests": {
                    "confirm": {
                        "type": "elicitation",
                        "message": f"{tier_msg}：即将执行 {name}，是否继续？",
                        "schema": {"type": "boolean"},
                    }
                },
                "requestState": state,
            })
        # 若带了确认但为否，中止
        if input_responses and input_responses.get("confirm") is False:
            return _ok(rpc_id, _content(f"用户取消了 {name}。"))

        # F3.2 镜像：tier 求值器只读 body.get(field) 顶层；dimensions.flip 嵌套
        # 不被 rules.json tier2（field=flip, value=true）命中。必须在这里镜像到
        # args["flip"] 顶层，hook 跑 check_body 时才能拦下「flip=true 缺 flip_mode」。
        # 必须放在 _run_hook 之前，否则 hook 已错过 tier 求值。
        if name in ("dedup_video", "batch_fission"):
            dims = args.get("dimensions")
            if isinstance(dims, dict) and dims.get("flip"):
                args = dict(args)
                args["flip"] = True

        # --- PreToolUse hook：拦截 + 校验 + 补全 ---
        pre = _run_hook("pre_tool_guard.py", {
            "hook_event": "PreToolUse", "tool_name": name, "tool_input": args, "tier": tool["_tier"],
        })
        if not pre.get("continue", True):
            return _ok(rpc_id, _content(f"❌ 被 Hook 拦截：{pre.get('reason', '未通过安全校验')}"))
        if pre.get("modifiedInput"):
            args = pre["modifiedInput"]

        # --- 执行 ---
        try:
            result = _exec_tool(name, args)
        except Exception as e:
            _run_hook("post_tool_audit.py", {
                "hook_event": "PostToolUse", "tool_name": name, "tool_input": args,
                "status": "error", "error": str(e),
            })
            return _ok(rpc_id, _content(f"❌ 执行失败：{e}", is_error=True))

        # --- PostToolUse hook：审计 ---
        _run_hook("post_tool_audit.py", {
            "hook_event": "PostToolUse", "tool_name": name, "tool_input": args,
            "status": "ok", "result_summary": _summary(name, result),
        })

        return _ok(rpc_id, _content(json.dumps(result, ensure_ascii=False, indent=2)))

    return _err(rpc_id, -32601, f"未知方法: {method}")


def _summary(name, result):
    if name == "dedup_video":
        checks = result.get("checks") or {}
        return {
            "output": result.get("output_path"),
            "checks": checks,
            "phash": checks.get("phash"),
            "phash_passed": bool(checks.get("phash", {}).get("passed")),
            "applied_level": (result.get("applied_params") or {}).get("level"),
        }
    if name == "batch_fission":
        wrapper = result.get("matrix") or {}
        inner = wrapper.get("matrix") if isinstance(wrapper, dict) else []
        off = []
        if isinstance(inner, list):
            for i, row in enumerate(inner):
                if not isinstance(row, list):
                    continue
                for j, v in enumerate(row):
                    if i == j or v is None:
                        continue
                    if isinstance(v, bool) or not isinstance(v, (int, float)):
                        continue
                    off.append(float(v))
        return {
            "count": result.get("count"),
            "all_unique": result.get("all_unique"),
            "all_pass": wrapper.get("all_pass") if isinstance(wrapper, dict) else None,
            "off_diagonal_mean": round(sum(off) / len(off), 3) if off else None,
            "off_diagonal_min": round(min(off), 3) if off else None,
        }
    return {"ok": True}


def _content(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _ok(rpc_id, result):
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _err(rpc_id, code, message):
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# HTTP 传输：POST /mcp
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # 静默默认日志

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, MCP-Protocol-Version, Mcp-Method, Mcp-Name")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        if self.path.rstrip("/") not in ("/mcp", ""):
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception:
            self._respond({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            return

        headers = {
            "protocol": self.headers.get("MCP-Protocol-Version", ""),
            "method": self.headers.get("Mcp-Method", ""),
            "name": self.headers.get("Mcp-Name", ""),
        }
        resp = handle_rpc(req, headers)
        self._respond(resp)

    def _respond(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _port_in_use(host, port):
    """探测端口是否已被占用（防止起多个实例导致'脑裂'：
    多实例各读各的 audit.jsonl，强制走链判定会时好时坏）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def serve(host="127.0.0.1", port=8765):
    # 端口独占检测：已有实例在跑就明确报错退出，绝不叠加第二个实例
    if _port_in_use(host, port):
        print(
            f"[MCP] 端口 {host}:{port} 已被占用——可能已有一个 server 在跑。\n"
            f"      为避免多实例'脑裂'（强制走链判定失效），本次启动已中止。\n"
            f"      如需重启：先结束占用 {port} 的进程，再运行本脚本。",
            file=sys.stderr,
        )
        sys.exit(3)

    class _Server(ThreadingHTTPServer):
        # 关闭地址重用：Windows 下 SO_REUSEADDR 会允许多进程绑同端口 → 脑裂根因
        allow_reuse_address = False

    srv = _Server((host, port), Handler)
    print(f"[MCP 2026-07-28] video-dedup-station 无状态服务已启动: http://{host}:{port}/mcp")
    srv.serve_forever()


if __name__ == "__main__":
    serve()
