# Change: 云 TTS 音频轨道替换

## Why

当前去重管线只动画面像素（crop/flip/speed/trim），音频轨原封不动。平台的去重检测同时比对视频指纹和音频指纹——画面变了但音频完全一样，照样可能被标重。需要在画面变换的基础上，补齐音频维度的唯一性。

## What Changes

- **新增** `tts_client.py`：封装 MiMo TTS v2.5 API，4 个中文音色（冰糖/茉莉/苏打/白桦），支持语速控制
- **扩展** `pipeline.py` dedup_video/batch_fission：增 tts_text/tts_voice/tts_speed 可选参数
- **扩展** `mcp_server.py`：新增 list_voices 工具，透传 TTS 参数
- **新增** ffmpeg 内嵌字幕自动提取：get_subtitle_text()，无 TTS 文案时自动用字幕文本
- **扩展** 前端：步骤 2 增 AI 配音控件（快捷填入 + 音色/语速选择）
- **修复** os.replace 绕过 safe-delete 沙箱；apad 静音补齐时长对齐

## Impact

- Affected specs: tts-audio-replace（新增）
- Affected code: `station/server/tts_client.py`（新）、`pipeline.py`、`mcp_server.py`、`web/index.html`、`web/app.js`、`web/style.css`、`requirements.txt`
- 新增依赖：`openai>=1.0`（MiMo API 客户端）
- 向后兼容：tts_text 为空时完全不触发，行为与改动前一致
