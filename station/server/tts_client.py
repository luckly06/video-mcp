# -*- coding: utf-8 -*-
"""
tts_client.py — MiMo TTS v2.5 语音合成客户端

封装小米 MiMo 平台语音合成 API（OpenAI 兼容协议），用于视频去重工位的
音频轨道替换功能。

依赖：openai>=1.0（pip install openai）
环境变量：MIMO_API_KEY — MiMo 平台 API Key
"""

import os
import base64
import logging
from pathlib import Path

_MIMO_API_KEY = os.environ.get("MIMO_API_KEY", "")
_MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"

# 预置中文音色列表（mimo-v2.5-tts 模型）
VOICES = {
    "冰糖": {"id": "冰糖", "lang": "zh", "gender": "女"},
    "茉莉": {"id": "茉莉", "lang": "zh", "gender": "女"},
    "苏打": {"id": "苏打", "lang": "zh", "gender": "男"},
    "白桦": {"id": "白桦", "lang": "zh", "gender": "男"},
}

logger = logging.getLogger("tts_client")


def _client():
    """惰性加载 OpenAI 客户端（避免 import 失败阻塞模块加载）。"""
    if not _MIMO_API_KEY:
        return None
    try:
        from openai import OpenAI  # noqa: E402
    except ImportError:
        logger.warning("openai 未安装，TTS 不可用。pip install openai")
        return None
    return OpenAI(api_key=_MIMO_API_KEY, base_url=_MIMO_BASE_URL)


def is_available():
    """检查 TTS 客户端是否可用（API Key 已设置且 openai 已安装）。"""
    if not _MIMO_API_KEY:
        return False
    try:
        from openai import OpenAI  # noqa: F401
    except ImportError:
        return False
    return True


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
        RuntimeError: API Key 未设置或 openai 未安装
        Exception: API 调用失败
    """
    client = _client()
    if client is None:
        raise RuntimeError(
            "MiMo TTS 不可用：请设置环境变量 MIMO_API_KEY 并 pip install openai"
        )

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
        completion = client.chat.completions.create(
            model="mimo-v2.5-tts",
            messages=[
                {
                    "role": "user",
                    "content": f"用自然、清晰的普通话朗读以下文本{speed_hint}。"
                },
                {
                    "role": "assistant",
                    "content": text,
                },
            ],
            audio={
                "format": output_format,
                "voice": voice_id,
            },
        )

        message = completion.choices[0].message
        audio_bytes = base64.b64decode(message.audio.data)
        return audio_bytes

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
