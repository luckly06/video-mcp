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
import re
import json
import uuid
import base64
import hashlib
import hmac
import mimetypes
import socket
import threading
import subprocess
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as P  # noqa: E402

PROTOCOL_VERSION = "2026-07-28"
SERVER_INFO = {"name": "video-dedup-station", "version": "1.0.0", "title": "视频去重数字员工"}

_STATION = Path(__file__).resolve().parent.parent
_HOOKS = _STATION / "hooks"
_LOGS = _STATION / "logs"
_WEB = _STATION / "web"
_WEB_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}

# 长任务 handle 存储（stateless 协议 + 显式 handle 应用状态）
# key: job_id -> {status, result, ...}。文件持久化，任意进程可读。
_JOBS_FILE = _LOGS / "jobs.json"

# requestState 是客户端可见的无状态确认句柄。进程内随机密钥用于给
# name + arguments 签名，防止第二阶段请求替换用户已确认的参数。
_REQUEST_STATE_KEY = os.urandom(32)

# 当前进程内正在运行的批量任务。仅保存不可序列化的取消令牌，生命周期随请求结束；
# job handle 仍按既有方式持久化到 jobs.json。
_ACTIVE_FISSION_LOCK = threading.Lock()
_ACTIVE_FISSION = {}


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


def _request_state(name, args):
    """生成绑定当前工具名和参数的无状态确认令牌。"""
    payload = json.dumps(
        {"name": name, "arguments": args},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    body = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(_REQUEST_STATE_KEY, body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def _valid_request_state(state, name, args):
    """验证 requestState 未被篡改，且仍绑定当前 name + arguments。"""
    if not isinstance(state, str) or "." not in state:
        return False
    body, signature = state.rsplit(".", 1)
    expected = hmac.new(_REQUEST_STATE_KEY, body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        padded = body + "=" * (-len(body) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception:
        return False
    return decoded == {"name": name, "arguments": args}


# ---------------------------------------------------------------------------
# 工具定义（tools/list 返回；带 ttlMs / cacheScope 供缓存）
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "list_assets",
        "description": "列出工程根 input/ 目录下可处理的视频素材（名称/大小）。",
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
                "task_id": {"type": "string", "description": "可选：客户端生成的批量任务句柄，用于运行中取消"},
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
    """执行 hook；PreToolUse 故障拒绝执行，PostToolUse 故障仅报告。"""
    fail_closed = script == "pre_tool_guard.py"

    def failed(reason):
        return {
            "continue": not fail_closed,
            "permissionDecision": "deny" if fail_closed else "allow",
            "reason": reason,
            "hook_error": reason,
        }

    path = _HOOKS / script
    if not path.exists():
        return failed(f"Hook 缺失: {path}")
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", "replace").strip()
            return failed(f"Hook 退出码 {proc.returncode}: {stderr or script}")
        out = proc.stdout.decode("utf-8", "replace").strip()
        if not out:
            return failed(f"Hook 无结构化输出: {script}")
        result = json.loads(out)
        if not isinstance(result, dict) or not isinstance(result.get("continue"), bool):
            return failed(f"Hook 输出无效: {script}")
        return result
    except Exception as e:
        return failed(f"Hook 执行异常: {e}")


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
        # task_id 是客户端可见的运行句柄，仅用于本进程内取消，不写入 shell 参数。
        task_id = args.get("task_id") or ("fission_" + uuid.uuid4().hex[:16])
        if not isinstance(task_id, str) or not task_id.startswith("fission_") or len(task_id) > 64:
            raise P.PipelineError("批量任务 task_id 无效。")
        token = P.CancellationToken()
        with _ACTIVE_FISSION_LOCK:
            if task_id in _ACTIVE_FISSION:
                raise P.PipelineError(f"批量任务已存在: {task_id}")
            _ACTIVE_FISSION[task_id] = token
        try:
            r = P.batch_fission(
                args["src"],
                count=args.get("count"),
                params=args.get("params"),
                level=args.get("level"),
                dimensions=args.get("dimensions"),
                flip_mode=args.get("flip_mode"),
                cancel_token=token,
            )
        finally:
            with _ACTIVE_FISSION_LOCK:
                _ACTIVE_FISSION.pop(task_id, None)
        r["task_id"] = task_id
        r["job_id"] = _new_job("fission", {
            "src": r["src"], "count": r["count"],
            "requested_count": r.get("requested_count"),
            "cancelled": r.get("cancelled", False),
        })
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
            state = _request_state(name, args)
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
        # 若带了确认但为否，中止；确认执行必须携带与原调用绑定的 requestState。
        if input_responses and input_responses.get("confirm") is False:
            return _ok(rpc_id, _content(f"用户取消了 {name}。"))
        if tool["_tier"] in ("warned", "blocked"):
            if not isinstance(input_responses, dict) or input_responses.get("confirm") is not True:
                return _ok(rpc_id, _content(
                    "❌ 人工确认响应无效：confirm 必须为 true。", is_error=True))
            if not _valid_request_state(params.get("requestState"), name, args):
                return _ok(rpc_id, _content(
                    "❌ 人工确认状态无效或与当前参数不匹配，请重新发起操作并确认。",
                    is_error=True,
                ))

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


def _allowed_host(host):
    """仅允许浏览器或客户端访问已配置的监听地址。

    默认白名单: 127.0.0.1 / localhost。
    可通过环境变量 VU_ALLOWED_HOSTS（逗号分隔）追加域名/IP，
    例如: VU_ALLOWED_HOSTS=vu.evenblue.top,124.71.209.36
    """
    if not isinstance(host, str) or not host:
        return False
    value = host.strip().lower()
    # IPv6
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0 or value[:closing + 1] != "[::1]":
            return False
        suffix = value[closing + 1:]
        return suffix == "" or (suffix.startswith(":") and suffix[1:].isdigit())
    # 分离 hostname 与端口
    if ":" in value:
        hostname, port = value.rsplit(":", 1)
        if not port.isdigit():
            return False
    else:
        hostname = value
    # 白名单：默认 + 环境变量
    allowed = {"127.0.0.1", "localhost"}
    extra = os.environ.get("VU_ALLOWED_HOSTS", "")
    if extra:
        allowed.update(h.strip().lower() for h in extra.split(",") if h.strip())
    return hostname in allowed


def _allowed_origin(origin):
    """允许非浏览器请求、file:// 的 null Origin 与已配置的域名/IP。"""
    if origin in (None, "", "null"):
        return True
    try:
        parsed = urlsplit(origin)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    allowed = {"127.0.0.1", "localhost"}
    extra = os.environ.get("VU_ALLOWED_HOSTS", "")
    if extra:
        allowed.update(h.strip().lower() for h in extra.split(",") if h.strip())
    return parsed.hostname in allowed


def _cancel_fission(task_id):
    """向当前进程内的批量任务发送取消信号。"""
    if not isinstance(task_id, str) or not task_id.startswith("fission_"):
        raise ValueError("批量任务 task_id 无效")
    with _ACTIVE_FISSION_LOCK:
        token = _ACTIVE_FISSION.get(task_id)
    if token is None:
        return False
    token.cancel()
    return True


def _open_output_folder(filename=None):
    """打开固定 output 目录；Windows 上可安全选中目录内的指定文件。"""
    output_dir = P.OUTPUT_DIR.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = None
    if filename:
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError("产物文件名无效")
        candidate = (output_dir / filename).resolve()
        try:
            candidate.relative_to(output_dir)
        except ValueError as exc:
            raise ValueError("产物文件必须位于 output/ 内") from exc
        if not candidate.is_file():
            raise FileNotFoundError(f"产物文件不存在: {filename}")
        target = candidate

    if os.name == "nt" and target is not None:
        subprocess.Popen(["explorer.exe", "/select,", str(target)])
    elif os.name == "nt":
        os.startfile(str(output_dir))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(target)] if target else ["open", str(output_dir)])
    else:
        subprocess.Popen(["xdg-open", str(output_dir)])
    return output_dir, target


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
            "delivery_ready": result.get("delivery_ready"),
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


def _parse_multipart(body, content_type):
    """简易 multipart/form-data 解析。返回 {field_name: (filename, data_bytes)}。"""
    if "boundary=" not in content_type:
        raise ValueError("缺少 boundary")
    boundary = content_type.split("boundary=", 1)[1].strip()
    boundary = boundary.strip('"')
    boundary_bytes = boundary.encode()
    result = {}
    for part in body.split(b"--" + boundary_bytes):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        headers_raw = part[:header_end].decode("utf-8", errors="ignore")
        data = part[header_end + 4:]
        if data.endswith(b"\r\n"):
            data = data[:-2]
        name = filename = None
        for line in headers_raw.split("\r\n"):
            low = line.strip().lower()
            if low.startswith("content-disposition:"):
                for piece in line.split(";"):
                    piece = piece.strip()
                    if piece.startswith("name="):
                        name = piece[5:].strip().strip('"')
                    elif piece.startswith("filename="):
                        filename = piece[9:].strip().strip('"')
        if name:
            result[name] = (filename, data)
    return result


# ---------------------------------------------------------------------------
# HTTP 传输：POST /mcp
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # 静默默认日志

    def _cors(self):
        origin = self.headers.get("Origin")
        if origin == "null":
            self.send_header("Access-Control-Allow-Origin", "null")
        elif origin and _allowed_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, MCP-Protocol-Version, Mcp-Method, Mcp-Name")

    def _request_allowed(self):
        return (_allowed_host(self.headers.get("Host"))
                and _allowed_origin(self.headers.get("Origin")))

    def do_OPTIONS(self):
        if not self._request_allowed():
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        self._serve_web(head_only=False)

    def do_HEAD(self):
        self._serve_web(head_only=True)

    def _serve_web(self, head_only=False):
        if not self._request_allowed():
            self.send_response(403)
            self.end_headers()
            return
        path = urlsplit(self.path).path
        # ── 文件下载 ──
        if path.startswith("/local/download/"):
            rel = unquote(path[len("/local/download/"):])
            try:
                safe = P._resolve_safe(rel, P.OUTPUT_DIR, must_exist=True)
            except Exception:
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
            body = safe.read_bytes()
            ct, _ = mimetypes.guess_type(str(safe))
            self.send_response(200)
            self.send_header("Content-Type", ct or "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{safe.name}"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        web_file = _WEB_FILES.get(path)
        if web_file is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        filename, content_type = web_file
        body = (_WEB / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def do_POST(self):
        if not self._request_allowed():
            self.send_response(403)
            self.end_headers()
            return
        # ── 文件上传 ──
        if self.path.rstrip("/") == "/local/upload":
            ct = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ct:
                self._respond_http(400, {"ok": False, "message": "需要 multipart/form-data"})
                return
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                parts = _parse_multipart(raw, ct)
                file_item = parts.get("file")
                if not file_item or not file_item[0]:
                    self._respond_http(400, {"ok": False, "message": "未收到文件"})
                    return
                filename, data = file_item
                # 只允许常见视频/音频扩展名
                safe_exts = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".ts", ".webm", ".m4v"}
                ext = Path(filename).suffix.lower()
                if ext not in safe_exts:
                    self._respond_http(400, {"ok": False, "message": f"不支持的文件类型: {ext}"})
                    return
                P.VIDEO_DIR.mkdir(parents=True, exist_ok=True)
                dest = P.VIDEO_DIR / filename
                dest.write_bytes(data)
                self._respond_http(200, {
                    "ok": True,
                    "filename": filename,
                    "path": str(dest),
                    "size": len(data),
                })
            except Exception as exc:
                self._respond_http(500, {"ok": False, "message": f"上传失败: {exc}"})
            return
        if self.path.rstrip("/") in ("/local/open-output", "/local/cancel-fission"):
            endpoint = self.path.rstrip("/")
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("请求体必须是 JSON 对象")
                if endpoint == "/local/cancel-fission":
                    found = _cancel_fission(payload.get("task_id"))
                else:
                    output_dir, selected = _open_output_folder(payload.get("filename"))
            except (ValueError, FileNotFoundError) as exc:
                self._respond_http(400, {"ok": False, "message": str(exc)})
                return
            except Exception as exc:
                action = "取消批量裂变" if endpoint == "/local/cancel-fission" else "打开输出文件夹"
                self._respond_http(500, {"ok": False, "message": f"无法{action}: {exc}"})
                return
            if endpoint == "/local/cancel-fission":
                self._respond_http(200, {
                    "ok": True,
                    "found": found,
                    "message": "已发送取消请求" if found else "任务已结束或不存在",
                })
            else:
                self._respond_http(200, {
                    "ok": True,
                    "path": str(output_dir),
                    "selected": str(selected) if selected else None,
                })
            return
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

    def _respond_http(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond(self, obj):
        self._respond_http(200, obj)


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
    host = os.environ.get("VU_HOST", "127.0.0.1")
    port = int(os.environ.get("VU_PORT", "8765"))
    serve(host, port)
