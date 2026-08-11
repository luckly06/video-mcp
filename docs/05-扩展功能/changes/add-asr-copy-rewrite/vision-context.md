# 视觉语义理解前置 · 自动视频画面描述

## 语义提取优先级（定稿）

```
有字幕轨 → 提取字幕原文            # 精准
无字幕   → ffmpeg 抽帧 + 识图      # 视觉兜底
ASR      → 降级为辅助参考          # 太不准，不作为主要来源
```

关键原则：**字幕和识图不是互斥的**。有字幕时字幕做主，但识图画面描述可作为补充上下文一并注入 prompt。

## 问题

ASR 识别不准时（背景音乐、音效、方言），DeepSeek 拿到离谱原文（如"没以"），改写文案与视频内容完全无关。

用户不愿手动描述视频内容。

## 方案

### 核心链路

```
探测素材 → has_subtitle=true?
           │YES → ffmpeg 提取字幕文本 → 作为"原文"
           │NO  → ffmpeg 抽取 3-5 帧 + 识图 → 产出"画面描述"作为"原文"
           │
           └→ 同时抽 3-5 帧（无论有无字幕）→ 识图产出"视觉补充"
           → DeepSeek 改写 prompt 结构:
             ## 视觉上下文（识图）
             [画面描述]
             ## 字幕原文（如有）
             [字幕文本]
             ## 要求
             [角色模板 + 字数限制]
```

### 关键技术点

| 环节 | 做法 |
|------|------|
| 关键帧选取 | ffmpeg `select='not(mod(n,floor(duration*fps/5)))'` 均匀抽 5 帧 |
| 帧压缩 | JPEG quality=60，尺寸限制 512px，控制嵌入体积 |
| 识图模型 | 复用已登录 DeepSeek 网页（同一个 Playwright context），用其原生识图/文件上传能力 |
| 注入方式 | 点击 DeepSeek 附件上传按钮 → 上传帧图片 → 填入 prompt → 等待识图回复 → 合并为画面描述字符串 |
| prompt 结构 | `## 视觉上下文\n[画面描述]\n\n## 角色\n[模板]\n\n## 要求\n[字数限制]\n\n## 原文\n[ASR 文本]` |

### 备选 / 降级

| 方案 | 优点 | 缺点 |
|------|------|------|
| DeepSeek 网页识图（首选） | 零 API 成本，复用已登录会话 | UI 自动化脆弱（文件上传控件难以操控） |
| 本地视觉模型（CLIP/BLIP） | 稳定，不依赖网页 | 需额外下载模型，算力要求高 |
| 云端视觉 API | 准确 | 需要 Key，有成本 |

### 渐进路线

1. **Phase 1（当前）**：`视频内容简述` 手动输入框已上线，用户填了就传，不填也能跑
2. **Phase 2**：ffmpeg 帧提取 + 本地轻量图像标签（如 MobileNet 分类），注入简单标签
3. **Phase 3**：Playwright 操控 DeepSeek 网页识图，完整自动化视觉理解

### 入口文件

- `station/server/copy_rewriter.py` — `_build_prompt` 目前接受 `topic` 参数，换成自动生成的视觉描述
- `station/server/pipeline.py` — 在 rewrite 之前新增帧提取步骤
- `station/server/mcp_server.py` — `rewrite_copy` 工具透传视觉描述

## 参照方法论（用户提供 · 认知流程层框架）

```yaml
# 视频作为多模态输入：同时走 Semantic(字幕) + Perceptual(画面)
cognition_layer:
  step_0_modality_router:
    video_input: [text(subtitle), perceptual(frames)]

  step_1_retrieve:
    semantic:    # 字幕/ASR -> 向量检索背景知识 (Neo4j + Qdrant)
    perceptual:  # 关键帧 -> CLIP/BLIP 多模态向量 (Multimodal Qdrant)

  step_2_rerank:
    semantic:    Score_s = (0.7*sim + 0.3*graph) * (0.8 + 0.4*imp)
    perceptual:  Score_p = sim^0.6 * rec^0.2 * imp^0.2

  step_2.5_normalize:
    S_s' = (S_s - μ_s) / σ_s   # z-score，批次内
    S_p' = (S_p - μ_p) / σ_p

  step_3_select:
    # 视频无字幕：w_s=0, w_p=1.0
    # 视频有字幕：w_s=0.4, w_p=0.6
    unified_score = w_s * S_s' + w_p * S_p'
    k_context = 3  # 最终注入 prompt 的语义条数

  step_3.5_build_context:
    # 画面描述 + 字幕 + 知识节点 -> working memory items
    # 注入 DeepSeek rewrite prompt

  step_4_working_memory:
    # 本轮推理的活跃上下文注入 Context Window

  step_5_memory_decision:
    # 判断改写结果是否值得沉淀为长期记忆
    # 通过后异步写回 Storage Layer
```

> 首版可用 z-score；若后期分布重尾明显，可改为 robust scaling (median/IQR)。
> k_retrieve (检索规模) 与 k_context (上下文容纳规模) 是两个独立参数，不能混为同一个 K。

