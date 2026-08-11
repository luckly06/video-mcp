# Design: add-asr-copy-rewrite

## Context

全链路 TTS 音频替换需要：视频原对白 → ASR/字幕 → 文案改写 → TTS 语音。已实现字幕提取 ✅，缺 ASR 兜底和文案优化。

## Decisions

### 1. ASR 选型：sherpa-onnx

**选择**：sherpa-onnx（本地离线，ONNX 推理）
**放弃**：MiMo ASR（¥0.5/小时，收费），whisper.cpp（量化版精度差）

| | sherpa-onnx | MiMo ASR |
|---|---|---|
| 成本 | 0 | ¥0.5/h |
| Python 支持 | pip + prebuilt wheels | openai SDK（同 TTS） |
| 中文精度 | 好 | 很好 |
| 模型大小 | ~239MB | 无本地 |

模型存放：`F:\Download\A-models\sherpa-onnx`（遵循跨项目约定）

### 2. DeepSeek 文案改写：Playwright + openteam 技术

**选择**：Playwright 操控 DeepSeek 网页，复用用户 Chrome 已登录会话
**核心技术（来自 openteam）**：
- `page.evaluate()` 在 contenteditable 元素注入文本 + 派发 InputEvent
- 轮询等待 `isGenerating` 结束 → 用 DOM selector 抓取最后一条 .ds-markdown 回复

**放弃**：DeepSeek API（增加外部付费依赖，违背 0 API Token 原则）

### 3. 文案改写：通用 system prompt + 用户模板（可选）

文案改写由两部分 prompt 组成，最终发给 DeepSeek：

```
[可选] 用户模板（角色/自定义指令）
↓
通用 system prompt（短视频配音文案优化师）
↓
需要改写的原文
```

**3.1 通用 system prompt（写死在 `copy_rewriter.SYSTEM_PROMPT`）**

```
你是短视频配音文案优化师。

## 任务
将输入的原始对白/字幕，改写为一条适合 TTS 配音的短视频旁白。

## 要求
1. 口语化：自然说话语气，不要书面语
2. 有钩子：开头 3 秒抓注意力（悬念/提问/冲击性陈述）
3. 有行动：结尾留互动引导（"点赞关注"、"评论区聊聊"等）
4. 时长适配：30-100 字，适合 15-60 秒短视频
5. 纯文案：只输出最终文案，不要解释、前缀、标注
```

**3.2 用户模板（用户自由填写，可选）**

- 形态：TTS 模式区里的 textarea，`id="tts-template"`
- 内容：任意字符串。常见用法是「角色设定」或「风格指南」，例如：
  - "你是带货主播，把原文改写为口播带货文案，突出卖点、引导下单，语气热情有感染力。"
  - "你是科普博主，把原文改写成严谨的科普讲解，避免夸张表达。"
- 留空：跳过 DeepSeek 改写，直接用原文 TTS
- 前端辅助：三个示例按钮（📦带货/🎙️解说/📱Vlog）一键填入示例文本到 textarea，**仅是快捷填入，用户可任意编辑或清空**

**3.3 prompt 拼接逻辑**（`copy_rewriter._rewrite_async`）

```python
parts = []
if template:
    if template in REWRITE_TEMPLATES:        # 内置预设键
        parts.append("## 角色\n" + REWRITE_TEMPLATES[template])
    else:                                    # 用户自由输入
        parts.append("## 自定义指令\n" + template)
parts.append(SYSTEM_PROMPT)
parts.append("需要改写的原文：" + original_text)
prompt = "\n\n".join(parts)
```

### 4. 管道：原文 → 可选改写 → TTS

```
[视频]
   │ ffmpeg probe → 检查是否有字幕轨
   │
   ├─ 有字幕轨 ─────────→ get_subtitle_text → raw_text
   │
   └─ 无字幕轨 ──→ ffmpeg 抽 16kHz WAV ──→ sherpa-onnx ASR → raw_text
                                                    │
                                                    ▼
                              ┌──── 用户提供模板 (rewrite_template)? ────┐
                              │                                          │
                              ▼                                          ▼
                       有模板：DeepSeek 改写                         无模板：跳过
                       （Playwright 操控网页）                  （直接用 raw_text）
                              │                                          │
                              ▼                                          ▼
                            tts_text                          tts_text（=raw_text）
                                    \                              /
                                     \                            /
                                      ▼                          ▼
                                            MiMo TTS → 新语音
                                                    │
                                                    ▼
                                ffmpeg 合并（apad + shortest）→ 最终 mp4
```

**文案来源优先级**：

| 优先级 | 来源 | tts_source 标签 |
|---|---|---|
| 1 | 用户手动输入文案 | `user` |
| 2 | 内嵌字幕提取（无改写） | `subtitle` |
| 3 | ASR 识别（无改写） | `asr` |
| 4 | 内嵌字幕 + DeepSeek 改写 | `subtitle_rewrite` |
| 5 | ASR 识别 + DeepSeek 改写 | `asr_rewrite` |
| - | 模板提供但改写失败 | `subtitle_fallback` / `asr_fallback`（用原文兜底） |

## Risks

| 风险 | 缓解 |
|---|---|
| sherpa-onnx Python wheel 不兼容 | 提供手动下载 + 路径配置指引 |
| DeepSeek 网页版改 DOM | 维护 selector 白名单，失效时降级跳过改写 |
| Playwright 被 DeepSeek 识别为 bot | `headless=False` + 使用用户真实 Chrome Profile |

## Dependencies

- `sherpa-onnx`（pip 或手动安装）
- `playwright`（pip install playwright）
- ONNX 模型文件放到 `F:\Download\A-models\sherpa-onnx`
- DeepSeek 网页已登录（需用户在 Chrome 登录一次）
