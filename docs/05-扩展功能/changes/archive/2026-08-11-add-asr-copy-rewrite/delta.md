# Delta: add-asr-copy-rewrite

## ADDED Features

### F-配音-005: sherpa-onnx 本地语音识别

视频无内嵌字幕轨时，用 sherpa-onnx 对音频进行本地离线语音识别，提取原文。

- **输入**：视频文件（无 subtitle 流）、sherpa-onnx 模型（`F:\Download\A-models\sherpa-onnx`）
- **核心逻辑**：
  - ffmpeg 将视频音频抽取为 16kHz 单声道 WAV
  - sherpa-onnx `OfflineRecognizer.from_paraformer()` 加载 ONNX 模型
  - `create_stream()` → `accept_waveform()` → `decode_stream()` → 文本
  - `is_available()` 检查模型文件是否存在（模型缺失 → 降级跳过，不阻塞去重）
- **预期产出**：
  - `station/server/asr_client.py` 新建
  - `requirements.txt` 新增 `sherpa-onnx`, `soundfile`
  - 模型存放约定：`F:\Download\A-models\sherpa-onnx\`
  - pipeline 在无字幕 + 无用户文案时自动调用 ASR

### F-配音-006: DeepSeek 文案改写（含用户模板）

获取字幕/ASR 原文后，通过 DeepSeek AI 将原文改写为适合 TTS 配音的短视频旁白。用户提供可选的「改写模板」控制改写风格。

- **输入**：
  - raw_text：字幕提取或 ASR 识别的原文
  - template（可选）：用户在前端自由填写的改写模板文本（任意字符串，如"你是带货主播，把原文改写为口播带货文案..."）
- **核心逻辑**：
  - Playwright 复用用户 Edge/Chrome 已登录的 DeepSeek 网页会话（chat.deepseek.com）
  - openteam 技术：`page.evaluate()` 在 contenteditable 注入文本 + `dispatchEvent(InputEvent)`
  - prompt 拼接：`[可选 用户模板]` + `通用 SYSTEM_PROMPT` + `原文`
  - 用户模板为空 → 不调 DeepSeek，直接用原文 TTS
  - 用户模板非空 → DeepSeek 改写 → 用改写结果 TTS
  - 改写失败 → 降级用原文（`tts_source` 加 `_fallback` 后缀）
  - 模板是**自由文本**（不受预设限制）；前端提供三个示例按钮（📦带货/🎙️解说/📱Vlog）一键填入模板 textarea，仅作快捷填充
- **预期产出**：
  - `station/server/copy_rewriter.py` 新建
  - `REWRITE_TEMPLATES` 内置预设字典（带货/解说/Vlog）
  - `rewrite(text, template=None)` 同步接口，template 支持任意字符串
  - `requirements.txt` 新增 `playwright`
  - pipeline 统一字幕/ASR 改写流程：
    - `rewrite_template` 非空 → 自动提取 → DeepSeek 改写 → TTS
    - `rewrite_template` 为空 → 自动提取 → 原文 TTS
    - `tts_source` 标记为 `subtitle_rewrite` / `asr_rewrite` / `subtitle_fallback` / etc.
    - 加 `tts_process` 追踪字段（前端报告可见完整链路）
  - 前端：复选框 `☑ 启用 DeepSeek 改写` + 模板 textarea + 示例填入 + 清空按钮
- **依赖**：
  - F-配音-001（MiMo TTS）+ F-配音-003（字幕提取）+ F-配音-005（ASR）

## MODIFIED Features

### F-配音-002: 去重音频轨道替换（扩展）

原功能仅支持用户手动输入文案或字幕提取。本次扩展增加：

- **新增参数**：`rewrite_template`（可选，默认 None）
- **文案来源扩展**：
  - 用户手动输入 → user（不变）
  - 字幕提取 + 无模板 → subtitle（不变）
  - 字幕提取 + 有模板 → subtitle_rewrite（新增）
  - ASR 识别 + 有模板 → asr_rewrite（新增）
  - 改写失败 → subtitle_fallback / asr_fallback（新增，用原文兜底）
- **tts_process 追踪**：`applied_params.tts_process` 记录完整流程（提取→是否改写→结果），前端报告展示
- **pipeline 修复**：有字幕轨但提取为空时，原来不回退到 ASR 的 bug 已修复

- **输入**：tts_text, tts_voice, tts_speed, rewrite_template（新增）
- **核心逻辑**：见上文 F-配音-006
- **预期产出**：
  - `pipeline.py` `dedup_video()` + `batch_fission()` 签名扩展
  - `mcp_server.py` 两个工具 schema + handler 透传 `rewrite_template`
  - 前端 `readTTS()` 返回 `rewrite_template` 字段
