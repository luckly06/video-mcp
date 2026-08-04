# Skills 层 — Mermaid SOP 流程编排

## Skills 层解决什么

对齐腾讯云文章 §3：MCP 工具层解决"能做什么"（原子能力），**Skills 层解决"怎么正确操作"**（编排与顺序）。工具本身不含流程知识——先探测还是先去重、失败了往哪回退、哪一步要人工确认，都由 skill 定义。

## 为什么用 Mermaid 流程图，而不是文字步骤列表

纯文字的"第一步…第二步…"对 Agent 有三个弱点：**顺序依赖**易被跳读、**条件分支**（成功/失败走不同路）难以线性表达、**失败回退**路径容易被忽略。Mermaid flowchart 用节点+箭头把这三者变成视觉结构：

- **顺序依赖**：箭头方向即执行先后，Agent 不易乱序。
- **条件分支**：判断节点 `{...}` 明确标注"成功/失败"两条边，Agent 知道每种结果走哪。
- **失败回退**：诊断节点回指到重试节点形成闭环，Agent 不会在失败后停摆或盲目继续。

结果是 Agent 更少跳步、乱序、漏掉自检。

## 强制走链机制

部分写工具有"前置必走"硬约束，写在 `shared/rules.json` 的 `chain_rules`，由 `hooks/pre_tool_guard.py` 在调用链路强制拦截（Agent 无法绕开）：

| 写工具 | 必须先走 | 原因 |
|--------|----------|------|
| `dedup_video` | `probe_video` | 未探测就去重可能用错参数 |
| `batch_fission` | `probe_video` | 裂变前须确认源素材信息 |
| `delete_output` | `list_jobs` | 删除前须确认删的是哪个产出 |

配合四级安全分级：`probe/list/get_job` = audit（只读放行）；`dedup/batch_fission/remove_watermark` = warned（弹窗人工确认）；`delete_output` = blocked（硬拦截，Web 工位手动确认）。

## Skill 索引

| Skill | 用途 | 核心走链 |
|-------|------|----------|
| [dedup-video](dedup-video/SKILL.md) | 单条视频去重（改 MD5、保分辨率） | probe → 确认 → dedup → get_job → 自检三项 → 失败诊断回退 |
| [batch-fission](batch-fission/SKILL.md) | 一素材裂变多变体分发 | probe → 确认数量 → fission → 校验 all_unique → 有重复重试 |
| [remove-watermark](remove-watermark/SKILL.md) | 按平台模板去水印 | list_templates → probe → 选平台 → remove（确认）→ 验片 |

## SKILL.md 约定

每个 skill 一个子目录，含一个 `SKILL.md`，frontmatter 声明 `name` / `description` / `tools` / `chain`，正文含：适用场景、前置条件、**SOP 流程图（Mermaid）**、步骤详解、失败处理。
