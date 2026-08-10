# docs/05-扩展功能 · 扩展功能工作区

> 遵循 OpenSpec 方法论（proposal → tasks → design → spec → archive），手动管理，不使用 CLI。

## DD 治理规则

| 规则 | 说明 |
|---|---|
| **SAD 冻结** | P0 阶段起冻结，不原地修改 |
| **小迭代** | 已有模块细节调整 → 原地升版本号（`video-uniqueness-DD-v1.x.md`） |
| **大扩展** | 新增功能/能力/架构范围 → 在此目录按 OpenSpec 流程管理 |
| **Archive** | 完工后将 `changes/<id>/` 整个搬入 `changes/archive/YYYY-MM-DD-<id>/` |

## 目录结构

```
05-扩展功能/
├── changes/                          # 进行中的变更
│   ├── add-tts-audio-replace/        # 🟢 活跃：TTS 音频替换
│   │   ├── proposal.md               # 为什么做 / 做什么
│   │   ├── tasks.md                  # 实现清单 + 进度
│   │   ├── design.md                 # 技术决策
│   │   └── specs/tts-audio-replace/  # 需求规格
│   │       └── spec.md               # ADDED Requirements + Scenarios
│   └── archive/                      # 已完工的变更
│
├── specs/                            # 已完工扩展的真相源（变更归档后落地）
├── 01-扩展功能需求提示词/            # AS-IS / Gap / TO-BE 需求分析模板
└── README.md                         # 本文件
```

## 当前活跃变更

| Change ID | 说明 | 进度 |
|---|---|---|
| `add-tts-audio-replace` | MiMo TTS 音频轨道替换 + ffmpeg 字幕提取 | 核心已实现，待 ASR + DeepSeek 文案改写 |
