## ADDED Requirements

### Requirement: sherpa-onnx 本地语音识别

系统 SHALL 在视频无内嵌字幕轨时，使用 sherpa-onnx 对音频进行本地语音识别。

#### Scenario: 无字幕视频 → ASR 识别成功
- **GIVEN** 视频无 subtitle 流，sherpa-onnx 模型已就绪
- **WHEN** 执行 dedup_video（tts_text 为空）
- **THEN** ffmpeg 抽取音频 WAV → sherpa-onnx 转录为中文文本；`tts_source="asr"`

#### Scenario: sherpa-onnx 模型缺失
- **GIVEN** 模型文件未下载到 `F:\Download\A-models\sherpa-onnx`
- **WHEN** 执行 ASR 识别
- **THEN** 跳过 ASR，TTS 仅依赖用户手动输入或内嵌字幕（降级不阻塞）

---

### Requirement: DeepSeek 文案改写（含用户模板）

系统 SHALL 在获取原始对白文本后，通过 DeepSeek 将其改写为适合 TTS 配音的旁白文案。改写时将通用 system prompt、用户提供的模板（可选）和原文合并发送给 DeepSeek。

#### Scenario: 原文案 + 用户模板 → 改写成功
- **GIVEN** DeepSeek 网页会话已登录，用户提供了改写模板（如"你是带货主播，把原文改写为带货口播..."）
- **WHEN** 调用 `copy_rewriter.rewrite(raw_text, template="带货")`
- **THEN** prompt 包含「## 角色」+ 模板内容 + system prompt + 原文；DeepSeek 返回改写后的文案

#### Scenario: 用户模板为空 → 跳过改写
- **GIVEN** 用户未填写改写模板（`rewrite_template` 为空字符串或 None）
- **WHEN** 执行 dedup_video 自动提取到字幕/ASR 原文
- **THEN** 不调用 DeepSeek；直接用原文案 TTS；`tts_source` 为 `subtitle` 或 `asr`（不带 `_rewrite` 后缀）

#### Scenario: 改写失败 → 降级原文
- **GIVEN** 用户提供了模板，但 DeepSeek 不可用（Playwright 未安装 / DOM 变化 / 网络问题）
- **WHEN** 调用 rewrite
- **THEN** 返回 None；pipeline 降级使用原始 asr/字幕文本作为 TTS 输入；`tts_source` 标记为 `subtitle_fallback` 或 `asr_fallback`

#### Scenario: DeepSeek 不可用 → 降级
- **GIVEN** Playwright 未安装或 DeepSeek 页面 DOM 变化
- **WHEN** 调用 rewrite
- **THEN** 返回 None，上层回退使用原始 asr 文本作为 TTS 输入

---

### Requirement: 改写模板（用户自由填写，optional）

用户 SHALL 能够在 TTS 区输入一段自由文本作为「改写模板」，该模板会在 pipeline 中与字幕/ASR 原文一起发送给 DeepSeek。模板为空时跳过 DeepSeek 改写。

#### Scenario: 用户在模板 textarea 填写自定义指令
- **GIVEN** 用户在自动模式下查看改写模板 textarea
- **WHEN** 用户输入 "你是科普博主，把原文改写成严谨的科普讲解，避免夸张表达。"
- **THEN** `readTTS()` 返回 `rewrite_template` 为该文本；去重时 pipeline 将其作为「## 自定义指令」注入 prompt

#### Scenario: 模板留空
- **GIVEN** 用户在自动模式下模板 textarea 为空
- **WHEN** 去重启动
- **THEN** pipeline 跳过 DeepSeek 改写，直接用字幕/ASR 原文 TTS

#### Scenario: 点击示例按钮填入模板
- **GIVEN** 自动模式下的三个示例按钮（📦带货/🎙️解说/📱Vlog）
- **WHEN** 用户点击"📦 带货"
- **THEN** textarea 填入对应的示例模板文本，用户可在此基础上自由编辑或清空

---

### Requirement: 全链路文案来源优先级

系统 SHALL 按以下优先级确定 TTS 文案：

1. 用户手动输入的 `tts_text`
2. 视频内嵌字幕轨自动提取（如模板为空，不改写）
3. sherpa-onnx ASR 识别（如模板为空，不改写）
4. 字幕提取 + DeepSeek 改写（用户提供了模板）
5. ASR 识别 + DeepSeek 改写（用户提供了模板）
6. 改写失败降级为原文（模板提供了但 DeepSeek 不可用）

#### Scenario: 用户手动输入 → 最高优先
- **GIVEN** 用户填了 TTS 文案
- **WHEN** 执行 dedup_video
- **THEN** 直接使用用户输入，不触发字幕提取/ASR/改写；`tts_source="user"`

#### Scenario: 有字幕 + 有模板 → 字幕改写
- **GIVEN** 视频有 subtitle 流，用户提供了改写模板
- **WHEN** 执行 dedup_video（tts_text 为空，rewrite_template 非空）
- **THEN** ffmpeg 抽取字幕 → 和模板一起发给 DeepSeek → 用改写结果 TTS；`tts_source="subtitle_rewrite"`
