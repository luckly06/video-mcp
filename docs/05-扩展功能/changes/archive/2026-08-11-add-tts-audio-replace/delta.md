# Delta: add-tts-audio-replace

## ADDED Features

### F-配音-001: MiMo TTS v2.5 客户端封装

在 `station/server/tts_client.py` 封装 MiMo 平台语音合成 API（OpenAI 兼容协议），提供文本→语音转换能力。

- **输入**：用户提供的配音文案（string）、音色（冰糖/茉莉/苏打/白桦）、语速倍率（0.5-2.0）
- **核心逻辑**：
  - 惰性加载 OpenAI 客户端：`is_available()` 先检查 `MIMO_API_KEY` 环境变量 + openai 包是否可导入
  - `tts(text, voice, speed)` → 调用 `mimo-v2.5-tts` 模型 → 返回 WAV bytes
  - `tts_to_file()` / `tts_to_temp()` 便捷封装
  - `list_voices()` 返回 4 种中文音色列表
  - MIMO_API_KEY 由 mcp_server.py 启动时自动从 `.env` 加载（不依赖 python-dotenv）
  - 无 Key 或 openai 未安装 → `is_available()=False`，`tts()` 抛 RuntimeError
- **预期产出**：
  - `station/server/tts_client.py` 新建
  - `station/server/.env` 含 MIMO_API_KEY
  - `mcp_server.py` 新增 `list_voices` MCP 工具
  - `requirements.txt` 新增 `openai>=1.0`

### F-配音-002: 去重音频轨道替换

在单条去重流程中，用 TTS 生成的 AI 语音替换视频原音频轨。

- **输入**：tts_text（用户文案或自动提取）、tts_voice、tts_speed
- **核心逻辑**：
  - `pipeline.dedup_video()` 新增 tts_text/tts_voice/tts_speed 参数
  - 流程：ffmpeg 生成去重产物 → TTS 生成 WAV → ffmpeg 合并（-map 0:v:0 -map 1:a:0）
  - apad 滤镜补齐静音（TTS 短于视频）+ -shortest 截断（TTS 长于视频）
  - 合并通过 os.replace() 原子替换输出文件（绕过 safe-delete 沙箱拦截）
  - 失败时保留原始音轨，applied_params 记录 tts_warning
- **预期产出**：
  - `pipeline.py` `dedup_video()` 新增 TTS 流程段
  - `mcp_server.py` `dedup_video` / `batch_fission` 工具透传 TTS 参数
  - 前端步骤 2 新增 AI 配音控件组（音色/语速/文案）
  - `batch_fission` 也支持 TTS 参数透传

### F-配音-003: ffmpeg 内嵌字幕自动提取

视频有内嵌字幕轨时，自动用 ffmpeg 提取字幕文本作为 TTS 配音文案。

- **输入**：视频文件路径（已通过 probe_video 检测 has_subtitle）
- **核心逻辑**：
  - `get_subtitle_text()` 用 ffmpeg -map 0:s:0 提取 srt/ass 字幕流
  - 清洗时间戳和标记，输出纯文本
  - `probe_video()` 新增 has_subtitle/subtitle_codec 字段
  - pipeline 在 tts_text 为空且 has_subtitle=True 时调用
- **预期产出**：
  - `pipeline.py` `get_subtitle_text()` / `probe_video()` 扩展

### F-配音-004: TTS 音频时长对齐

确保 TTS 生成的音频时长与视频对齐，不因篇幅差异产生音画不同步。

- **输入**：TTS WAV 文件 + 已去重视频
- **核心逻辑**：
  - TTS 短于视频 → `-filter_complex [1:a]apad[a]` 尾部补静音
  - TTS 长于视频 → `-shortest` 按视频时长截断
  - 使用 `c:a aac -b:a 128k` 编码为标准 AAC 音频轨
- **预期产出**：
  - 合并后的 mp4 时长 = 视频时长，音频连续无断层
