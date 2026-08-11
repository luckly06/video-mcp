# F-配音-007: 自动视频语义理解 · 视觉上下文注入

## Change ID

`add-vision-context`

## 影响级别

**B** — 新增系统能力（视觉理解），跨模块改动（copy_rewriter + pipeline + mcp_server）

## 问题

ASR（sherpa-onnx Paraformer）在不理想音频条件下识别严重不准（如"剑鬼.mp4"产出"没以"），DeepSeek 拿到离谱原文后改写出的文案与视频内容完全无关。用户不愿意/无力手动描述每个视频的内容。

当前 `视频内容简述` 输入框是临时手动缓解，需要自动化。

## 方案总览

```
探测素材 → has_subtitle?
            ├── YES → ffmpeg 提取字幕文本 → 作为改写原文 (gold standard)
            └── NO  → ffmpeg 抽取 3-5 帧关键画面 (JPEG)
                    → 画面送入视觉理解通道
                    → 产出「视频画面描述」
                    → 作为改写原文 (visual fallback)
                    
改写 Prompt 结构:
  ## 视觉上下文
  [画面描述 / 字幕文本]
  
  ## 角色
  [用户模板/带货/解说/Vlog]
  
  ## 要求
  [字数限制 by 视频时长]
  
  需要改写的原文：[原文/画面描述]
```

## 两个实现路径

### 路径 A（推荐先行）：DeepSeek 网页识图

复用已登录的 DeepSeek 浏览器会话，用 playlist-replay 操作 DeepSeek 的识图/文件上传 UI。

**步骤**：
1. ffmpeg 均匀抽样 5 帧 → JPEG（512px max, quality=60）
2. Playwright 同一 context 下点击 DeepSeek "附件/图片"上传按钮
3. `page.setInputFiles()` 上传帧图片
4. 填入 prompt："请用一句话描述这些画面里拍的是什么场景、有什么人物或动作"
5. 等待识图回复
6. 合并回复作为 `视觉上下文` 注入改写 prompt

**优点**：零 API 成本，零新依赖
**风险**：DeepSeek UI 改动会破坏上传控件 selector；单次改写需额外一次网络往返

### 路径 B（长期稳健）：本地视觉模型

**轻量版**：MobileNet/CLIP 分类标签（场景标签、物体标签）
**完整版**：BLIP-2 / LLaVA 本地图像描述

## 渐进里程碑

| Phase | 做什么 | 产出 |
|-------|-------|------|
| Phase 1 (当前) | `视频内容简述` 手动输入框 | 已上线，用户填了就传 `topic` |
| Phase 2 | ffmpeg 帧提取 + DeepSeek 网页识图 | 自动，无字幕视频也能产出靠谱文案 |
| Phase 3 | 路径 B 本地视觉模型 + 知识图谱优化 | 离线可用，不需联网 |

## 要改的文件

| 文件 | 改动 |
|------|------|
| `station/server/copy_rewriter.py` | 新增 `_extract_frames()` + `_vision_describe()`；`_build_prompt` 已支持 `topic` 参数，扩展为自动视觉描述 |
| `station/server/pipeline.py` | `probe_video` 后可调用帧提取和视觉描述 |
| `station/server/mcp_server.py` | `rewrite_copy` 工具自动触视觉理解链路；新增 `vision_describe` 独立工具便于调试 |

## 可参照的方法论

```yaml
# 用户提供的认知流程层框架（用于语义检索与记忆）
cognition_layer:
  modality_router:
    input_type: video  # 视频 → 需同时走 Semantic(字幕) + Perceptual(画面)
    task_type: content_description

  retrieve:
    semantic:    # 字幕/ASR 文本 → 向量检索相关背景知识
      backend: [Neo4j, Qdrant]
    perceptual:  # 关键帧 → CLIP/BLIP 多模态向量
      backend: [Multimodal Qdrant]

  rerank:
    semantic_weight: 0.7 * sim + 0.3 * graph
    perceptual_weight: 0.6 * sim + 0.2 * rec + 0.2 * imp

  select:
    w_s: 0.4   # 字幕权重
    w_p: 0.6   # 画面权重（无字幕时 1.0）
    k_context: 3  # 最终注入上下文的信息条数

  build_context:
    # 将画面描述 + 字幕文本 + 知识图谱节点
    # 压缩为结构化 working memory items
    # 注入 DeepSeek prompt

  memory_decision:
    # 改写成功的文案 + 视频特征 → 写入 Storage Layer
    # 形成可复用的语义记忆
```

## 关键文件
- `docs/05-扩展功能/changes/add-asr-copy-rewrite/vision-context.md` — 技术方案详稿
- `station/server/copy_rewriter.py` — 改写核心，视觉注入点
- `station/server/pipeline.py` — 管道入口，帧提取触发点
