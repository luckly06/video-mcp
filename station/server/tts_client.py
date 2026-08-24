# -*- coding: utf-8 -*-
"""
tts_client.py — MiMo TTS v2.5 语音合成客户端

封装小米 MiMo 平台语音合成 API（OpenAI 兼容协议），用于视频去重工位的
音频轨道替换功能。

依赖：仅 Python 标准库
环境变量：MIMO_API_KEY — MiMo 平台 API Key
"""

import os
import base64
import json
import logging
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request

_MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"

# ── 自动加载同目录 .env（MIMO_API_KEY 等）──────────────────────────────
# 关键修复：桌面端 local-server.js 拉起后端时不会注入 MIMO_API_KEY，
# 而 tts_client 此前只在模块导入时读一次 os.environ，导致 is_available()
# 永远为 False、TTS 被静默跳过（文案在但产物没配音）。这里在导入时把
# station/server/.env 载入 os.environ，并改为延迟读 key 消除导入时序坑。
def _load_dotenv():
    """把模块同目录下的 .env 载入 os.environ（已存在的变量不覆盖）。"""
    try:
        env_path = Path(__file__).resolve().parent / ".env"
        if not env_path.exists():
            return
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


_load_dotenv()


def _api_key():
    """延迟读取 MIMO_API_KEY（不在导入时缓存）。"""
    return os.environ.get("MIMO_API_KEY", "")

# 预置中文音色列表（mimo-v2.5-tts 模型）
VOICES = {
    "冰糖": {"id": "冰糖", "lang": "zh", "gender": "女"},
    "茉莉": {"id": "茉莉", "lang": "zh", "gender": "女"},
    "苏打": {"id": "苏打", "lang": "zh", "gender": "男"},
    "白桦": {"id": "白桦", "lang": "zh", "gender": "男"},
}

logger = logging.getLogger("tts_client")


def _client():
    """兼容旧调用方：有 Key 时返回轻量配置，没有时返回 None。"""
    key = _api_key()
    return {"api_key": key, "base_url": _MIMO_BASE_URL} if key else None


def is_available():
    """检查 TTS 客户端是否可用（发布态只要求 API Key 已注入）。"""
    return bool(_api_key())


def unavailable_reason():
    """返回明确的不可用原因，避免继续误报 openai 未安装。"""
    return "" if _api_key() else "未配置 MIMO_API_KEY"


def list_voices():
    """返回可用音色列表（供前端选择）。"""
    return [
        {"id": v["id"], "lang": v["lang"], "gender": v["gender"]}
        for v in VOICES.values()
    ]


def tts(text, voice="冰糖", speed=1.0, output_format="wav"):
    """调用 MiMo TTS 生成语音，返回音频字节数据。

    Args:
        text: 待合成文本（建议 1-200 字，超长文本应分段）
        voice: 音色名（冰糖/茉莉/苏打/白桦）
        speed: 语速（0.5-2.0，1.0=正常）
        output_format: wav 或 pcm16

    Returns:
        bytes: 音频数据

    Raises:
        RuntimeError: API Key 未设置
        Exception: API 调用失败
    """
    if not _api_key():
        raise RuntimeError("MiMo TTS 不可用：未配置 MIMO_API_KEY")

    voice_info = VOICES.get(voice, VOICES["冰糖"])
    voice_id = voice_info["id"]

    # 语速指令：通过 user message 自然语言控制
    speed_hint = ""
    if abs(speed - 1.0) > 0.01:
        if speed > 1.0:
            speed_hint = f"，语速加快约{int((speed-1)*100)}%"
        else:
            speed_hint = f"，语速放慢约{int((1-speed)*100)}%"

    try:
        payload = {
            "model": "mimo-v2.5-tts",
            "messages": [
                {
                    "role": "user",
                    "content": f"用自然、清晰的普通话朗读以下文本{speed_hint}。"
                },
                {
                    "role": "assistant",
                    "content": text,
                },
            ],
            "audio": {
                "format": output_format,
                "voice": voice_id,
            },
        }
        req = urllib_request.Request(
            f"{_MIMO_BASE_URL}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {_api_key()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        audio_data = result["choices"][0]["message"]["audio"]["data"]
        return base64.b64decode(audio_data)

    except urllib_error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:500]
        except Exception:
            detail = ""
        raise RuntimeError(f"MiMo TTS 调用失败: HTTP {e.code} {detail}".strip()) from e
    except Exception as e:
        raise RuntimeError(f"MiMo TTS 调用失败: {e}") from e


def tts_to_file(text, output_path, voice="冰糖", speed=1.0):
    """生成语音并保存到文件。

    Args:
        text: 待合成文本
        output_path: 输出文件路径（.wav）
        voice: 音色名
        speed: 语速

    Returns:
        Path: 输出文件路径

    Raises:
        RuntimeError: TTS 不可用或调用失败
    """
    audio_bytes = tts(text, voice=voice, speed=speed)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio_bytes)
    return path


def tts_to_temp(text, voice="冰糖", speed=1.0):
    """生成语音到临时文件，返回路径。调用方负责清理。

    Returns:
        Path: 临时 .wav 文件路径
    """
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="vu_tts_")
    os.close(fd)
    return tts_to_file(text, path, voice=voice, speed=speed)
