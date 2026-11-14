# AI 工作流

本文面向修改 `backend/cinecontext/ai` 的开发者。总体分层与事务设计见 [系统架构](architecture.md)。

## 状态图

```text
PLAN --success--> DISCOVER --success--> ASSESS
ASSESS --follow_up--> FOLLOW_UP --success--> ASSESS
ASSESS --success--> SELECT
SELECT --success--> TIMELINE --success--> GUIDE
SELECT --partial--> PUBLISH_PARTIAL --success--> COMPLETED
GUIDE --retry--> GUIDE
GUIDE --success/partial--> RATINGS
RATINGS --success--> PUBLISH --success--> COMPLETED
RATINGS --partial--> PUBLISH_PARTIAL --success--> COMPLETED
runner 未处理异常 --> CurationService --> FAILED
```

`FAILED` 是 runner 的 terminal state 类型之一，但没有普通 transition 指向它；未处理异常离开 runner 后，由 `CurationService` 调用 persistence 写入领域 run 的 `failed` 状态。

## 核心类型与接口

[`workflow.py`](../backend/cinecontext/ai/workflow.py) 定义编排原语：

| 类型 | 契约 |
|---|---|
| `WorkflowContext` | 每次 run 唯一的可变上下文；节点原地修改，不得替换对象 |
| `WorkflowNode` | 只有 `async run(ctx) -> ctx` 的 Protocol |
| `CurationState` | 当前节点身份；terminal 为 `COMPLETED`/`FAILED` |
| `NodeOutcome` | 节点结果：`SUCCESS`、`FOLLOW_UP`、`RETRY`、`PARTIAL` |
| `WorkflowTransition` | `(source, outcome, optional guard) -> target` 的不可变规则 |
| `TransitionEngine` | 对当前 state/outcome 求唯一下一状态 |
| `WorkflowRunner` | 清空旧 outcome、执行节点、检查契约、转移，最多 32 步 |

`TransitionEngine.next_state` 必须恰好匹配一条规则。零条表示状态表不完整，多条表示规则有歧义；两者都会抛 `WorkflowTransitionError`。runner 还拒绝未注册 state、未设置 outcome、返回新 context 或超过 step limit。

## Typed Context 与 Enums

context 中的业务对象不是任意 dict：constraints/plan/guide 来自 [`contracts.py`](../backend/cinecontext/contracts.py)，film/evidence/decision/study/timeline 来自 [`ai/types.py`](../backend/cinecontext/ai/types.py)。`StrEnum` 的 value 是 API、数据库审计和 JSON 的稳定外部值，例如 `needs_evidence`、`web_excerpt`、`history`。

`require_plan()`、`require_study()`、`require_guide()` 把状态前置条件转成明确错误。新增节点不应在缺少前置产物时静默生成默认业务结果。

## 节点职责

| 节点 | 主要行为 |
|---|---|
| `PlanNode` | 模型生成搜索 query 和 film hints |
| `DiscoverNode` | 查本地候选、可选 TMDB hints、每片本地/网页证据、主题 context |
| `AssessNode` | 模型逐片判定；过滤越权 evidence；决定是否 follow-up |
| `FollowUpNode` | 对一个 film/query 补充证据，再只重评该 film |
| `SelectNode` | primary 优先、截断数量、生成 study 并持久化；无入选则 partial |
| `TimelineNode` | 模型研究 story/myth/history；builder 加入 release 轴并过滤无效关系 |
| `GuideNode` | 生成/修订 guide，执行覆盖与引用归属校验 |
| `RatingsNode` | 可选并发 OMDb lookup；不决定电影是否入选 |
| `PublishNode` | 完整或数量不足的 guide 以 completed/partial 结束 |
| `PublishPartialNode` | 无 guide 或无 selected film 时以 partial 结束 |

节点对象不保留 run 状态，因此可安全复用；当前 `create_workflow` 每个 run 会创建新节点集合。所有 run 状态都在 context 或数据库中。

## 有界循环

工作流有四层防无限循环约束：

| 常量 | 当前值 | 含义 |
|---|---:|---|
| `MAX_FOLLOW_UP_QUERIES` | 4 | run 内不同 `(film_id, query)` 总数 |
| `MAX_FOLLOW_UPS_PER_FILM` | 2 | 单片补证次数 |
| `MAX_GUIDE_ATTEMPTS` | 2 | 初次生成加一次修订 |
| `MAX_WORKFLOW_STEPS` | 32 | runner 总节点执行次数 |

follow-up 还要求 accepted 数量未达到请求数量、decision 是 `needs_evidence`、query 非空且此前未见。无新 evidence 时不再重评该片。

## Timeline Builder

[`timeline.py`](../backend/cinecontext/ai/timeline.py) 将可信来源分成两类：

- `release` 完全从已选 film 的 catalog releases 确定性构造，不需要模型 evidence ID。
- `story`、`myth`、`history` 来自模型 `TimelineResearch`，必须通过 evidence、film ownership 和轴规则验证。

builder 丢弃重复 event ID、引用未发布 evidence、引用未选 film 的事件。myth 必须有 version 且不能伪造 `sort_year`；history 必须有 `sort_year`。order edge 两端需存在、不能自环、version 相同、evidence 有效。拓扑排序按 layer 生成 `order_group`；出现环时无法释放的剩余事件不会进入该 view。

## Guide Validator

[`guide_validation.py`](../backend/cinecontext/ai/guide_validation.py) 检查：

1. `film_guides` 恰好覆盖每部 selected film 一次。
2. `viewing_order` 恰好覆盖每部 selected film 一次。
3. 每个 claim 的 evidence ID 存在于本 run 发布 evidence。
4. film claim 只能引用该 film 或 `film_id=None` 的公共 evidence。

第一次失败时，截断到 600 字符的错误文本通过 `validation_issue` 反馈给模型修订。第二次仍失败则继续 ratings，最终发布 partial 且不保存 guide。

## 工具审计与持久化

当前“工具调用”由工作流节点确定性编排，不是 OpenAI原生 `tools/function calling`。现有工具接口、调用节点、Provider选择和未来原生 Function Calling的扩展约束见 [工具调用与 Function Calling](function-calling.md)。

`WorkflowOperations.logged` 包装模型与工具动作，使用 monotonic clock 记录 operation、`ok/empty/failed`、elapsed milliseconds 和 detail。Operation 名集中在 `OperationName` enum。失败审计完成后重新抛出原异常。

`optional` 只吞掉 `httpx.HTTPError`、`ValueError`、`KeyError`，为可降级外部工具添加 limitation；模型主路径或未列出的异常仍失败。`capture` 以 `(film_id, source_url, body)` 在内存去重，持久化后才把带数据库 UUID 的 `EvidenceRecord` 加入 context。

每次 persistence 方法进入新的 Alcedo scope/UoW。不要把 HTTP/model await 移进 `@transactional` 方法，否则会扩大事务和锁的持续时间。

## 失败行为

- Plan、核心 assess 或其他未处理错误：离开 runner，由 `CurationService` 写 `FAILED`。
- Model timeout/rate limit/auth/connection：failure facade 转换为面向用户的稳定消息。
- TMDB、web、ratings 的指定可选错误：记录 limitation 后继续。
- Timeline 模型 `APIError`/`ValueError`：保留 release timeline，添加 limitation 后继续。
- Guide API error：不重试 validation cycle，进入 ratings 后 partial publish。
- 进程退出：当前 background task 丢失；下次启动把 queued/running run 标记为 interrupted。

## 安全增加节点

1. 在 `CurationState` 增加 state，并确认是否真的需要新的可观察步骤而不是扩展现有节点。
2. 实现无实例 run 状态的 `WorkflowNode`，原地修改 context，并在所有返回路径设置 outcome。
3. 在 `create_workflow` 注册 node，并为每个可能 outcome 声明唯一 transition。
4. 如引入循环，增加业务上限，同时确认总步数 32 足够覆盖最坏路径。
5. 外部调用使用 `WorkflowOperations.logged/optional`；外部 I/O 与持久化事务分离。
6. 新 context 数据使用 typed model/dataclass/enum，并通过 `require_*` 表达前置条件。
7. 补充 transition、runner contract、边界和序列化 shape 测试。

## 安全增加模型 Provider

1. 实现 `ApplicationModel` 的 `parse/plan/assess/timeline/generate/close` 和 `mode`。
2. 实现 `ApplicationModelProvider.create()`；每次 run 返回可独立关闭的 client/model。
3. 所有模型返回值在 adapter 边界校验为 contracts，不把未经校验的 dict 交给节点。
4. 在新组合根或现有组合根中显式替换 `ApplicationModelProvider` 注册，并提供 typed Options。
5. 保持 prompt 中的输入/来源为 data，避免把检索文本当系统指令。
6. 验证错误类型是否能被 parse application service 和 `CurationService` 正确映射。

## 安全增加 Research Provider

1. 在 [`research_tools/main.py`](../backend/cinecontext/ai/research_tools/main.py) 的 `FilmMetadataProvider`、`WebSearchProvider` 或 `RatingsProvider` 协议下实现适配器。
2. 在独立 provider 文件中封装供应商协议、认证、限额和 response parsing。
3. 由 `create_research_tools` 明确选择优先级，不在节点中散布供应商判断。
4. 返回 typed `FilmProjection`、`EvidenceDraft` 或 `Rating`，并通过 facade 保持工作流接口稳定。
5. 对 crawler 类 provider 保留 SSRF、重定向、大小和 content-type 限制。
6. 增加 provider selection、解析、空结果、错误降级及持久化测试。
