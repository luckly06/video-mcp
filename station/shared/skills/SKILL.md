# Skills 层目录索引

> 本文件是 `station/shared/skills/` 的目录级索引，不是 MCP 工具，也不是一个额外注册的独立 Skill。实际可加载的技能只有三个子目录中的 `SKILL.md`：`dedup-video`、`batch-fission`、`remove-watermark`。
>
> 本索引不定义新的统一 stage、状态机、输入输出信封、幂等键或导出工具。调用协议以 `station/server/mcp_server.py`、`station/shared/rules.json` 和对应子技能为准。

## 技能注册

| Skill | 适用场景 | 工具 | 实际前置约束 |
|---|---|---|---|
| [dedup-video](dedup-video/SKILL.md) | 单条视频去重，改变 MD5 和画面特征并保持分辨率 | `probe_video`、`dedup_video`、`get_job` | `rules.json` 要求先成功调用 `probe_video`；当前 hook 只从审计日志恢复“调用过的工具名”，不校验前置调用是否对应本次 `src`，所以调用方仍须自行保证探测的是当前素材 |
| [batch-fission](batch-fission/SKILL.md) | 一条素材生成多个分发变体 | `probe_video`、`batch_fission`、`get_job` | 与去重相同：hook 能强制“此前有成功的 `probe_video`”，但不能证明它与本次 `src` 相同 |
| [remove-watermark](remove-watermark/SKILL.md) | 按平台模板去除固定坐标水印 | `list_watermark_templates`、`probe_video`、`remove_watermark` | `list_watermark_templates` 和 `probe_video` 是流程建议；当前 `rules.json` 未为 `remove_watermark` 配置强制走链，且该工具不返回 `job_id` |

运行时没有 `ingest`、`sanitize`、`export` 这几个独立工具或阶段名。通常由调用方按需求选择素材、探测、去水印、去重或裂变。`remove_watermark.output_path` 位于 `output/`，而 `probe_video`、`dedup_video` 和 `batch_fission` 的公开调用都把 `src` 限制在 `station/assets/`，hook 还拒绝绝对路径；因此去水印产物当前**不能直接**路由为后续去重或裂变的 `src`。如需串联，必须先通过工具协议之外的受控文件操作把产物放回 `station/assets/`，再用新素材名重新 `probe_video`。

## 实际输入字段

工具调用使用 MCP `tools/call` 的 `arguments` 对象，不使用本文件自定义的统一 `input` 包装。字段以 `tools/list` 返回的 schema 为准：

- `probe_video`：必填 `src`。
- `dedup_video`：必填 `src`；可选 `params`、`out_name`、`level`、`dimensions`、`flip_mode`、`seed`。启用 `dimensions.flip` 时必须提供 `flip_mode`；`level` 的默认补全由 `rules.json` 声明为 `medium`。
- `batch_fission`：必填 `src`、`count`；可选 `params`、`level`、`dimensions`、`flip_mode`。`count` 由规则补全时默认是 `5`，上限为 `20`；该工具没有 `seed` 或 `trim_phase` 输入。
- `list_watermark_templates`：无输入字段。
- `remove_watermark`：必填 `src`、`platform`；可选 `out_name`。
- `get_job`：必填 `job_id`，用于查询显式任务 handle。

`dedup_video`、`batch_fission`、`remove_watermark` 属于 `warned` 写工具。首次调用返回的实际判别字段是 `resultType: "input_required"`，并带 `requestState`；确认重发时，`inputResponses.confirm=true` 和 `requestState` 都放在 `tools/call.params` 顶层，与 `name`、`arguments` 平级。`probe_video`、`list_assets`、`list_watermark_templates`、`get_job` 属于只读审计工具。`delete_output` 是独立的 `blocked` 工具，不属于上述三个技能的交付链。

## 实际返回结构

MCP 成功响应是标准 `{content, isError}` 外壳，业务结果作为 `content` 中的 JSON 文本返回；不存在本文件另行规定的 `schema_version`、`run_id`、`stage`、`status`、`next_action` 或统一 `outputs` 信封。

- `dedup_video` 结果包含 `src`、`output`、`output_path`、`applied_params`、`checks`、`fps` 和服务端追加的 `job_id`。交付自检读取 `checks.md5_changed`、`checks.resolution_kept`、`checks.duration_close`、`checks.min_duration_ok` 与 `checks.phash.passed`；pHash 达标口径是 `phash_avg >= 12` 且 `weak_frame_ratio <= 0.10`，`phash_min` 仅展示。
- `batch_fission` 结果包含 `src`、`count`、`variants`、`all_unique`、`matrix`、`separation` 和服务端追加的 `job_id`。矩阵交付门是 `all_unique=true` 且 `matrix.all_pass=true`；对角线值为 `null`，过近对在 `matrix.too_close_pairs` 中。
- `remove_watermark` 结果包含 `src`、`platform`、`delogo_region`、`output_path`、`output_md5`；当前服务端不会为该工具追加 `job_id`。
- `get_job` 返回持久化的 `{job_id, kind, status, meta}` 任务记录。它不是对原始工具结果的统一恢复接口；当前 `meta` 仅保存去重的源/输出或裂变的源/count，不能从中读取 `checks`、`variants`、`matrix` 或 `separation`。

## 子技能文档边界

三个子技能已按本索引校准 `get_job`、`batch_fission` 公开参数、`remove_watermark` 返回值与产物串联限制。仍有一项运行时边界必须由调用方承担：

- `dedup-video/SKILL.md` 与 `batch-fission/SKILL.md` 要求先对同一 `src` 执行 `probe_video`；当前 hook 实际只按审计日志中的工具名判断，无法校验素材一致性。运行时协议与文档冲突时，以 `mcp_server.py`、`rules.json`、hook 和 pipeline 为准。

## Web 结果展示边界

当前 `station/web/app.js` 是直接调用原子工具并渲染原始结果：

- 去重结果卡片展示五项检查、pHash 详情、`applied_params` 和 `job_id`。
- 裂变结果卡片展示 `all_unique`、`matrix.all_pass`、变体列表、距离矩阵及 `separation` 诊断。
- 人工确认由 `callToolWithConfirm` 处理，取消和错误通过现有 toast 与记忆时间线记录。
- `station/README.md` 的手测步骤 5-7 实际是“确认后执行去重并检查结果”“验证删除仍被硬阻断”“验证审计日志落盘”。页面实现没有由顶层 Skill 定义的统一步骤状态机，也没有按 `run_id + stage` 去重 toast、质量门解锁或刷新后自动恢复统一信封的实现。
- 页面当前不会因 `checks.phash.passed=false` 或 `matrix.all_pass=false` 阻止交付；去重完成 toast 仍提示人工决定是否交付，裂变完成 toast 只报告生成数量。顶层索引只能把这些字段定义为人工交付判据，不能声称页面已实现自动质量门。

## 选择与回退

1. 需要单条成品时使用 `dedup-video`；需要多个变体时使用 `batch-fission`。
2. 声明存在固定平台水印时使用 `remove-watermark` 选择模板并人工确认。若还要去重或裂变，须先把产物受控复制回 `station/assets/`，再以新素材名执行 `probe_video`；当前工具协议不支持把 `output_path` 直接串给后续技能。
3. 去重或裂变质量检查失败时，按原写工具返回的检查字段调整参数并重跑写工具；只有更换 `src`、素材内容发生变化或尚未成功探测当前素材时才重新执行 `probe_video`。不得用 MD5 已变化替代 pHash 或矩阵质量检查，也不得依赖 `get_job` 恢复这些检查字段。
4. 所有强制链和安全分级以 `shared/rules.json` 与 hook 实际求值为准；本索引不新增或放宽规则。
