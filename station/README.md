# 视频去重数字员工 · Agent 工位

把 **CRVideoMate 的视频去重能力**封装成一个符合 **MCP 2026-07-28 无状态规范**的 Server，外面套一个本地 Web「Agent 工位」壳来调用。

> **为什么不直接驱动 CRVideoMate.exe？**
> `CRVideoMate.exe` 是闭源 GUI，无命令行接口、无 SDK，无法被程序驱动。
> 因此本项目用它**同捆的 ffmpeg**（`CR综合处理永久版（不要更新）/ffmpeg/`）复现其去重管线，
> 对应面板勾选项：画面微调、微旋转、帧率、降噪、码率、加/去水印、裂变。

---

## 一键启动

```bash
cd station
python run.py
```

`run.py` 会启动 MCP Server（`127.0.0.1:8765`）并自动用浏览器打开 Web 工位。
也可分开跑：

```bash
python server/mcp_server.py       # 起 server
# 然后双击 web/index.html
```

依赖：Python 3.x + 同捆 ffmpeg。数值 pHash 自检需先执行 `pip install -r requirements.txt`；缺少 `imagehash`/Pillow 时会自动降级为 ffmpeg signature 二值判定。

---

## 对齐两篇指标文章

### 指标一 · MCP + Skills + Hooks 三件套（腾讯云）

| 层 | 解决 | 本项目落地 |
|----|------|-----------|
| **MCP 层** | 能操作什么 | 8 个工具（list_assets / probe_video / dedup_video / batch_fission / list_watermark_templates / remove_watermark / get_job / delete_output）。遵循 **MCP 2026-07-28 无状态**：无 initialize 握手、无 Mcp-Session-Id、请求自带 `_meta`、`tools/list` 带 `ttlMs`/`cacheScope`、长任务用显式 `job_id` handle |
| **Skills 层** | 怎么操作才对 | `shared/skills/` 下 3 个 SKILL.md，每个含 **Mermaid SOP 流程图**（顺序依赖 / 条件分支 / 失败回退闭环）。**强制走链**：调 `dedup_video` 前必须先 `probe_video` |
| **Hooks 层** | 被允许怎么操作 | `hooks/` 三个 hook：PreToolUse（四级分级拦截 + tier 条件链字段校验 + 自动补全）、PostToolUse（审计落盘）、SessionStart（注入记忆/权限）。规则**全部外化**到 `shared/rules.json`，代码不硬编码 |

**四级安全分级**（`rules.json`）：

| 级别 | 含义 | 工具 |
|------|------|------|
| 🟢 pass | 静默放行 | check_env |
| 🟢 audit | 放行+审计 | list_assets, probe_video, get_job, list_watermark_templates |
| 🟠 warned | 弹窗确认 | dedup_video, batch_fission, add/remove_watermark |
| 🔴 blocked | 硬阻断 | delete_output（Agent 不可直接调用，须人工在 Web 工位确认） |

### 指标二 · Agent 工位（阿里·五层）

Web 壳（`web/`）呈现「数字员工工牌」，五层对应：

| 层 | 落地 |
|----|------|
| **身份** | 工牌卡片：工号 VD-2026-0728、职责边界、「持续在线」状态 |
| **权限** | 工具白名单 + 仅可访问 `video/` 素材目录 |
| **工具** | 左栏 MCP tools 清单（按四级分级上色） |
| **记忆** | 右栏审计时间线（localStorage + audit.jsonl 会话记忆还原走链状态） |
| **责任** | 每步可视化、审计流落盘、人工决策弹窗、blocked 硬阻断 |

**人机接力**（数字员工「持续在线、过程可控、结果可审计」）：
Agent 干到「70 分」（去重完成 + 五项自检：MD5 / 分辨率 / 时长 / ≥5s / pHash）→ 人工决策「是否交付 / 是否再生成变体」。
决策点用 **MCP 2026-07-28 的 `InputRequiredResult` + `requestState`**（SEP-2322 多轮请求）承载：
warned/blocked 工具首次调用返回 `input_required`，用户确认后**重发原请求**并回传 `requestState`——
任意 server 实例都能接住重试（无状态）。

---

## 目录结构

```
video-agent-station/
├── run.py                    # 一键启动（server + 打开 Web）
├── server/
│   ├── mcp_server.py         # MCP 2026-07-28 无状态 Server（POST /mcp）
│   └── pipeline.py           # ffmpeg 去重管线（复现 CRVideoMate 面板）
├── shared/
│   ├── rules.json            # 外化规则：四级分级 + tier 条件链 + 强制走链
│   └── skills/               # Mermaid SOP
│       ├── dedup-video/SKILL.md
│       ├── batch-fission/SKILL.md
│       └── remove-watermark/SKILL.md
├── hooks/
│   ├── common.py             # 规则加载 + tier 条件链求值器
│   ├── pre_tool_guard.py     # PreToolUse：拦截 + 校验 + 补全
│   ├── post_tool_audit.py    # PostToolUse：审计落盘
│   └── session_start.py      # SessionStart：注入记忆 + 权限
├── web/                      # Agent 工位壳（原生 HTML/CSS/JS，零依赖）
├── logs/
│   ├── audit.jsonl           # 审计日志
│   └── jobs.json             # 任务 handle 持久化
└── output/                   # 去重产出
```

---

## 端到端验证状态

本期已由实现侧用 `assets/下班来接我.mp4`（1070×1914 竖屏）执行以下 1–7 步，并形成 `docs/eval/F5.1-浏览器步骤5-7执行报告.md`；独立 QA 尚未签字，因此当前交付状态保持“等待 QA”，不得把实现侧结果或 QA 执行失败视为独立验收通过。完整复核项见 `web/README-手测清单.md`：

1. `server/discover` → 协议 2026-07-28 ✅
2. `tools/list` → ttlMs=300000 缓存元数据，8 工具 ✅
3. `probe_video` → 1070×1914, MD5 a9bfee34 ✅
4. `dedup_video` 无确认 → `input_required` + requestState（人工决策点）✅
5. `dedup_video` 带确认 → 执行并展示五项自检（MD5 / 分辨率 / 时长 / ≥5s / pHash），生成 `job_id` ✅ 实现侧 PASS / ⏳ 独立 QA 待签字
6. `delete_output` 带确认 → 仍被 Hook 第4级硬阻断拦截 ✅ 实现侧 PASS / ⏳ 独立 QA 待签字
7. 审计日志落盘，含 output 路径 + 自检结果 ✅ 实现侧 PASS / ⏳ 独立 QA 待签字

---

## 去重原理

改变 MD5 与视频特征（画面微调、微旋转、帧率、码率、元数据清除），**保持分辨率不变**，
绕过平台重复检测。裂变模式下同一素材生成多个 MD5 互异的变体。

## 边界说明

- 本工具仅做**素材去重处理**，不做剪辑、不做上传、不越权访问 `video/` 以外目录。
- `delete_output` 受硬阻断保护，需人工确认，防止误删产出。
- 不修改、不驱动同级 `CR综合处理永久版（不要更新）` 目录（仅只读引用其 ffmpeg 与水印模板）。
