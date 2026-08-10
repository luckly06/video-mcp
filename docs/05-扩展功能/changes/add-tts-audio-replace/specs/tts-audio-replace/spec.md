## ADDED Requirements

### Requirement: MiMo TTS 客户端封装

系统 SHALL 提供 `tts_client.py` 模块，封装 MiMo TTS v2.5 API 的语音合成能力。

#### Scenario: 单次 TTS 调用成功
- **GIVEN** 已设置 `MIMO_API_KEY` 且已安装 `openai` 包
- **WHEN** 调用 `tts_client.tts("你好世界", voice="冰糖", speed=1.0)`
- **THEN** 返回 24kHz WAV 格式的 `bytes` 音频数据

#### Scenario: 无 API Key 时降级
- **GIVEN** `MIMO_API_KEY` 未设置
- **WHEN** 调用 `tts_client.is_available()`
- **THEN** 返回 `False`，且 `tts()` 抛 `RuntimeError`

---

### Requirement: 去重音频轨道替换

系统 SHALL 在单条去重时，支持用 TTS 生成的语音替换视频原音频轨。

#### Scenario: 用户提供文案 → 音频替换成功
- **GIVEN** 用户上传了视频并在 TTS 文案框填入了文案
- **WHEN** 执行 dedup_video（tts_text 非空，TTS.is_available()=True）
- **THEN** 产物视频的音频轨为 TTS 生成的语音；`applied_params.tts_applied=True`

#### Scenario: 用户未填文案 → 不触发 TTS
- **GIVEN** 用户未填 TTS 文案
- **WHEN** 执行 dedup_video（tts_text=None）
- **THEN** 产物保留原始音频轨，行为与改动前完全一致

#### Scenario: TTS 失败 → 降级保留原音轨
- **GIVEN** TTS 生成或 ffmpeg 合并失败
- **WHEN** 执行 dedup_video
- **THEN** `applied_params.tts_warning` 记录失败原因，产物保留原始音轨

---

### Requirement: ffmpeg 内嵌字幕自动提取

系统 SHALL 在视频有内嵌字幕轨且用户未填 TTS 文案时，自动提取字幕文本作为配音文案。

#### Scenario: 有字幕轨 → 自动提取
- **GIVEN** 视频有 subtitle 流且用户未填 TTS 文案
- **WHEN** 执行 dedup_video
- **THEN** `get_subtitle_text()` 返回字幕纯文本；文案来源标记为 `"subtitle"`

#### Scenario: 无字幕轨 → 静默跳过
- **GIVEN** 视频无 subtitle 流
- **WHEN** 执行 dedup_video
- **THEN** TTS 不触发，不影响去重流程

---

### Requirement: TTS 音频时长对齐

系统 SHALL 确保 TTS 生成的音频时长与视频对齐。

#### Scenario: TTS 短于视频
- **GIVEN** TTS 生成的 WAV 比视频短
- **WHEN** ffmpeg 合并音频轨
- **THEN** `apad` 滤镜在 TTS 尾部自动补齐静音，产物时长 = 视频时长

#### Scenario: TTS 长于视频
- **GIVEN** TTS 生成的 WAV 比视频长
- **WHEN** ffmpeg 合并音频轨
- **THEN** `-shortest` 自动截断至视频时长
