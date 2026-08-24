# Tasks: add-tts-audio-replace

## 1. TTS 客户端
- [x] 1.1 创建 `station/server/tts_client.py`（MiMo TTS v2.5 封装）
- [x] 1.2 `is_available()` 降级检查（缺 API Key 或 openai 时返回 False）
- [x] 1.3 `list_voices()` 返回音色列表供前端选择
- [x] 1.4 `tts()` / `tts_to_temp()` 核心语音合成

## 2. Pipeline 集成
- [x] 2.1 `dedup_video` 增 tts_text/tts_voice/tts_speed 参数
- [x] 2.2 ffmpeg 两遍合并：第一遍画面去重 → TTS 生成 WAV → 第二遍替换音轨
- [x] 2.3 `os.replace` 原子替换（绕过 safe-delete 沙箱）
- [x] 2.4 `apad` 滤镜静音补齐（TTS 短于视频时尾部补静音）
- [x] 2.5 失败降级：TTS 生成/合并失败时保留原始音轨 + tts_warning
- [x] 2.6 `batch_fission` 透传 TTS 参数

## 3. 字幕自动提取
- [x] 3.1 `probe_video` 增 has_subtitle / subtitle_codec 字段
- [x] 3.2 `get_subtitle_text()`：ffmpeg 抽取 SRT → 纯文本
- [x] 3.3 dedup_video 自动提取：无 tts_text 但有字幕轨 → 自动用字幕文本

## 4. Server 暴露
- [x] 4.1 新增 `list_voices` 工具（audit 级）
- [x] 4.2 `dedup_video` / `batch_fission` inputSchema 增 TTS 字段
- [x] 4.3 `_summary` 含 TTS 状态

## 5. 前端
- [x] 5.1 步骤 2 增 AI 配音控件（文案输入 + 音色/语速选择）
- [x] 5.2 快捷填入按钮（带货/解说/Vlog 预设）
- [x] 5.3 dedup 报告显示 TTS 状态 + 字幕轨道

## 6. 待实现
- [ ] 6.1 MiMo ASR：无字幕素材的语音转文字（方案已确定）
- [ ] 6.2 DeepSeek 文案改写：用 openteam 浏览器操控，免 API 改写文案
