## ''开发管理(记忆)"与''运行时状态(质量)'"管理指南 (INDEX.md)

> 项目采用“开发活跃记忆”与“流转（Eval 级控制）”物理隔离的双轨制架构。本索引用于规范通用 Agent 在执行“任务式流程”时的状态机维护准则，并采用 **YAML-like 英文紧凑格式** 以最大化节省 Token 成本。
>
> **术语说明（通用化）**：全文以“任务（task）”为通用单元；若你的项目有更贴切的业务名词（订单/工单/会话/作业等），可整体替换 `task_id` 前缀，schema 结构不变。

### 1.`history/` — 开发与记忆轨（时间流水）（2 份主文件 + 归档目录）

>  history的记录需按照INDEX.md采用 ***\*****YAML****-like 英文紧凑格式**

- 负责管理 Agent 运行期间的活跃开发上下文与动态增量记忆，属于“干活与执行”视角。  

| 文件                                 | 一句话                                                       |
| ------------------------------------ | ------------------------------------------------------------ |
| [`开发过程.md`](history/开发过程.md) | 当前活跃区开发流水（21 KB；2026-05-22 起按守则维护；2026-05-21 之前的条目按月切到 `archive/`） |
| [`当前状态.md`](history/当前状态.md) | 真正的"项目当前快照"（3.6 KB；切片 1 视角；每跨阶段整块重写） |
| [`archive/`](history/archive/)       | 历史快照归档：`开发过程_2026-04_至_2026-05-06.md`、`开发过程_2026-05_前半.md`、`当前状态_2026-05_前快照_归档.md` |

> 维护守则写在两份主文件顶部。简言之：**指标→看板**、**多文件改动→链接到 acceptance**、**completed 任务立即移除**、**每月归档跨月条目**。500 KB 不再是触发阈值——按月切已成日常约束。

### 2. `eval/` — 状态管理与路由轨（本阶段定义）

负责管理任务的生命周期、边界约束与异常回滚熔断策略，属于宏观层面的“状态机内核”。

| **文件/目录**                                                | **一句话职责定位**                                           | **Token 优化策略**                             |
| ------------------------------------------------------------ | ------------------------------------------------------------ | ---------------------------------------------- |
| [`方案边界.md`](https://www.google.com/search?q=eval/方案边界.md) | **Task Boundary**：定义任务目标、范围、工具白名单与安全红线（含“何为完成”）。 | 静态约束，任务初始化时读取一次，禁止频繁刷新。 |
| [`验证结果.md`](https://www.google.com/search?q=eval/验证结果.md) | **Quality Snapshot**：记录当前任务的运行时进度、质量指标与交付物索引。 | 动态覆写，采用极简英文键值对，实时同步。       |
| [`沉淀失败原因.md`](https://www.google.com/search?q=eval/沉淀失败原因.md) | **Fallback & Escalation**：异常状态回滚点、容错策略与失败错题本。 | 仅在触发状态机异常分支时读写，沉淀错题本。     |
| [`archive/`](https://www.google.com/search?q=eval/archive/)  | 历史任务状态与异常 Postmortem 归档。                         | 任务结束（Success/Terminated）后整块移入。     |

##  `eval/` 通用状态管理核心模版 (YAML + English 节流格式)

<attenton>💡 **Skill Integration (特例集成提示)**： **当调用 `diagnosing-bugs` 这个 skill 时，可深度搭配 `eval/` 文件夹使用。** AI 必须将该 Skill 要求的“定义边界、最小化复现”记录于 `方案边界.md`；将“自动化回归测试结果”实时同步至 `验证结果.md`；若排查方向错误或中断，必须将根因与现场日志沉淀进 `沉淀失败原因.md`。</attention>

```
如果 Agent 领到的任务是：“给这个前端网页跑一套稳定的 E2E 交付测试”，那么在 eval/方案边界.md 的工具白名单里就可以放行贴合单站点的稳定型工具；但如果任务变成“跨多个站点的长流程抓取”，就必须在 方案边界.md 里把不适配的工具拉黑、换成更耐长流程的方案。要点是**边界随任务性质变**，具体工具名不写死在规范里。
```



## 🎯 `eval/` 场景适用性边界矩阵 (Scoping Matrix)

```
为了防止状态机模块过载，依据项目工程需求，对 `eval/` 文件夹的职责边界进行如下强制划分：

### ✅ 允许进入 `eval/` 的适用场景（运行时动态控制）
eg:
- 3、针对Web、移动端、企业后台、长流程任务等场景，设计简单、中等、复杂、困难四档用例，验证成功率、耗时、失败原因、恢复能力、维护
成本和接入成本。
- **多自动化工具链的动态路由路由管理**：当执行流在 `Claude Code`、`Claude Computer Use`、`Playwright`、`Selenium` 或 `Browser MCP` 之间进行热切换或工具链调用传递时，其状态令牌与路由白名单必须由 `方案边界.md` 和 `验证结果.md` 承载。
- **长流程任务的实时指标监控**：在针对 Web、企业后台等长流程场景执行时，动态收集并覆写当前的“验证成功率”**、**“实时耗时”**以及**“状态恢复能力（回滚 Checkpoint）”至 `验证结果.md`。
- **运行时异常根因动态沉淀**：一旦长流程任务发生非预期中断，系统必须将当前的“失败原因”、原始 Log 现场精准归因并写入 `沉淀失败原因.md`。
- **自动化中间件资产锚定**：任务执行成功后，生成的 `PoC` 路径、自动化 `脚本/测试用例` 的最终生成状态或 `代码 Diff 快照` 的索引，允许作为载荷写入 `验证结果.md` 的交付清单中。

### ❌ 严禁进入 `eval/` 的不适用场景（宏观静态/非运行时资产）
eg:
- 4、评估现有产品与自研方案的关系，明确哪些能力应购买、集成、借鉴或自研，避免重复造轮子。5、将调研结论转化为工程资产，包括PoC、脚本、测试用例、工具封装、能力矩阵、失败样本库、选型报告、技术方案和可复用模板。
- **静态工具横向对比与实测报告**：针对各大工具（如 Appium, Frida, RPA 等）的静态优劣势横向对比、实测技术文档，**不适用**于本文件夹，应存放于公共研究院或研发知识库中。
- **商业与战略评估决策**：关于“评估现有产品与自研方案的关系，明确哪些能力应购买、集成、借鉴或自研”等商业/架构选型决策，**绝不适用**于状态机，严禁将此类非结构化文本塞入大模型运行时上下文。
- **宏观全周期成本核算**：项目的“维护成本”**与**“接入成本”属于静态财务/管理指标，而非运行时性能指标，**不适用**于实时状态管理。
- **宏观技术资产与报告文本**：大篇幅的“选型报告”**、**“能力矩阵”**、长篇的**“技术方案”**或静态的**“可复用模板”，**不适用**于状态流转控制，这些属于静态工程资产，应统
```

为了让执行任务的 Agent 以最低 Token 成本秒懂当前状态，`eval/` 下的文档内部严禁使用多余的中文描述，统一遵循以下精简结构：

###  1. `eval/方案边界.md` (Topology & Constraint)

**【通用 Agent 定位】** 规定当前任务工作流（Workflow）的路由白名单与熔断止损红线。

~~~markdown
# Workflow Topology & Constraints

```yaml
task_id: "TASK-2026-XXXX"
goal: "one-line task objective"
definition_of_done:            # 何为完成（可验证的完成判据）
  - "acceptance criterion 1"
workflow_route:
  allowed_nodes: [planner, executor, critic]
  current_route: planner -> executor
tool_permissions:
  allow: [read_db, http_request, read_file]
  deny: [drop_table, execute_shell_danger]
runtime_limits:
  max_retry_per_node: 3
  timeout_seconds: 300
  token_budget_quota: 50000
esc_trigger:
  on_auth_fail: TERMINATE_AND_CALL_HUMAN
  on_quota_exceed: FALLBACK_LOW_COST_MODEL

~~~

###  2. `eval/验证结果.md` (Session Snapshot)
**【通用 Agent 定位】** 充当整个状态机的“内存寄存器（Registers）”，记录当前走到哪了、手里有什么数据。
```markdown
# Session Runtime Snapshot

```yaml
snapshot_timestamp: 2026-07-12T21:35:00Z
lifecycle_status: RUNNING # [INIT, RUNNING, WAITING_HUMAN, SUCCESS, FAILED]
execution_stack:
  current_node: executor
  parent_node: planner
  depth: 2
data_payload:
  input_prompt_hash: "0x7f8a9b"
  extracted_intent: "modify_interior_design"
  active_variables:
    target_theme: "minimalist"
    output_path: "history/src/"
metrics_cost:
  elapsed_seconds: 42
  accumulated_tokens: 12400
quality_metrics:                    # 通用质量三问（任何项目都填得出）
  success: null                     # true/false 任务是否达成 DoD
  context_health: OK                # OK / DEGRADED / LOST 上下文有没有膨胀失控
  human_takeover_count: 0           # 人工介入次数（越少越自主）
  note: "one-liner: 哪里顺 / 哪里还长"

```

###  3. `eval/沉淀失败原因.md` (Fallback & Failure Logs)
**【通用 Agent 定位】** 异常处理中心。当系统流转错误或 Tool 报错时，用于计算如何回滚状态并记录。
```markdown
# Fallback Mechanism & Exception Logs

```yaml
exception_event:
  node_failed: executor
  error_class: TOOL_TIMEOUT # [MODEL_HALLUCINATION, INSUFFICIENT_CONTEXT, PROTOCOL_ERROR]
  raw_exception: "Database response exceeded 5000ms at node_failed"
rollback_checkpoint:
  target_node: planner
  clear_dirty_data: true
  action: "RE_PLANNING_WITH_REDUCED_SCOPE"
historical_lessons:
  - issue: "Dynamic DOM variant missed"
    solution: "Inject strict CSS locator pattern in next prompt generation"
```

4,通用状态机运转流水线 (State Machine Lifecycle)

1. **【Load Config (读边界)】** Agent 框架接收任务，载入 `eval/方案边界.md` 的拓扑结构，初始化全局锁与工具白名单。
2. **【Execute & Record (跑开发)】** 底层执行 Agent 启动，在 `history/` 的 `当前状态.md` 与 `开发过程.md` 中以增量流水形式维持高频对话记忆[cite: 1]。
3. **【Sync Snapshot (写结果)】** 每当任务节点发生转移（如 Planner 交付给 Executor），底层代码自动将最新上下文变量与状态码覆写至 `eval/验证结果.md`。
4. **【Handle Exception (控异常)】** 
   - 若遭遇非预期中断，系统解析 `eval/沉淀失败原因.md` 中的 Fallback 策略，将 `eval/验证结果.md` 中的节点状态安全回滚到上一个 Checkpoint。
   - 若任务彻底失败且无法自愈，写入最终故障根因，并将整套状态打包归档至 `eval/archive/`。