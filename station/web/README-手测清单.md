# Web 工位手测清单

适用范围：`station/web/` 的 F4.1 控件、F4.2 自检/矩阵渲染，以及 MCP 人工确认和审计闭环。

## 前置条件

- [ ] 从 `station/` 启动 `python run.py`，页面显示 MCP 已连接。
- [ ] `station/assets/` 至少有一个可处理视频。
- [ ] 数值 pHash 主路径需安装 `requirements.txt`；未安装时允许走 `signature` 兜底，但必须明确区分两种口径。

## 步骤 1-4：连接、控件与确认协议

- [ ] 页面连接 `http://127.0.0.1:8765/mcp` 成功。
- [ ] 工具列表包含 8 个工具，分级颜色与 `shared/rules.json` 一致。
- [ ] 强度档默认 `medium`；裂变数量默认 `5`，可输入范围为 `1-20`。
- [ ] 六个维度可切换；`flip` 默认关闭，启用后 `flip_mode` 才可选择。
- [ ] 全部维度关闭时，去重/裂变只提示警告，不发送业务请求。
- [ ] 选择素材并执行探测后，页面显示分辨率、时长、fps 和 MD5。
- [ ] 首次调用 `dedup_video` 返回 `input_required + requestState`；确认后以同一 `requestState` 和 `params.inputResponses.confirm=true` 重发。
- [ ] 省略/篡改 `requestState`，或确认后替换 `name/arguments`，Server 必须拒绝且不执行工具。
- [ ] 非本机 `Host`/`Origin` 请求返回 HTTP 403；本机页面和 `file://` 页面仍可连接。

## 步骤 5：单条去重与五项自检

- [ ] 确认执行后生成输出文件和 `job_id`。
- [ ] 页面展示 `md5_changed`、`resolution_kept`、`duration_close`、`min_duration_ok`、`phash.passed` 五项。
- [ ] pHash 主路径显示 `method=phash`、`phash_avg`、`phash_min`、`weak_frame_ratio`；判定口径为 `phash_avg >= 12 && weak_frame_ratio <= 0.10`，`phash_min` 仅展示。
- [ ] `signature` 兜底显示“签名兜底”；`threshold.applied=false` 时不得把 `avg=0/min=0` 表述为数值门通过。
- [ ] 任一检查失败时页面使用失败样式并给出重跑提示，`确认交付` 保持禁用且点击保护不放行。

## 裂变矩阵

- [ ] 执行 `batch_fission` 后显示变体数量、`all_unique` 和距离矩阵。
- [ ] 矩阵对角线为自身，`too_close_pairs` 对应单元格高亮。
- [ ] `matrix.all_pass=false` 时显示 `separation` 诊断；短素材 `time_leg=absent` 时给出 flip 提示。
- [ ] 只有 `all_unique=true` 且 `matrix.all_pass=true` 时 `delivery_ready=true`，页面才显示双门通过的成功提示；否则明确提示当前不可交付。

## 步骤 6-7：硬阻断与审计

- [ ] `delete_output` 首次调用返回 `input_required + requestState`。
- [ ] 确认重发后仍被第 4 级 `blocked` 硬阻断，目标输出文件仍存在。
- [ ] `station/logs/audit.jsonl` 含 `probe_video` 与写工具记录，摘要包含输出和自检结果。
- [ ] `station/logs/jobs.json` 中对应 `job_id` 状态为 `completed`。

## 独立 QA 签字

```yaml
executor:
execution_time:
asset:
phash_path: phash | signature
steps_1_4: PASS | FAIL
step_5: PASS | FAIL
fission_matrix: PASS | FAIL | NOT_RUN
step_6: PASS | FAIL
step_7: PASS | FAIL
independent_qa_reviewer:
independent_qa_signoff: PASS | FAIL | PENDING
notes:
```

独立 QA 未签字时，项目生命周期保持 `WAITING_HUMAN`；实现侧执行通过不能替代独立签字。
