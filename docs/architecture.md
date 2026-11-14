# 系统架构与设计原则

本文描述当前代码所实现的边界，不把规划中的能力写成既有事实。AI 状态机细节见 [AI 工作流](ai-workflow.md)，运行配置见 [部署与配置](deployment.md)。

## 设计原则

1. **证据先于生成**：候选电影必须有与主题相关的非 metadata 证据才能被接受；指南和研究时间线只能引用当前 run 已持久化的证据 ID。
2. **显式状态而非隐式链式调用**：AI 流程由 node、outcome、transition 和 runner 表达，重试与降级都是可见状态转移。
3. **端口隔离外部系统**：模型、研究工具、仓储与 curation persistence 均通过 Protocol 暴露应用所需的最小接口。
4. **短事务、外部 I/O 不入事务**：网络模型/API 调用在数据库事务外执行；每次持久化操作使用新的 Alcedo scope 和短 UoW。
5. **production/mock 共用业务流程**：两者只在组合根注入的模型与启动数据不同，工作流、数据库和 API 保持一致。
6. **结构化边界校验**：Pydantic contracts、typed enum、timeline builder 和 guide validator 在模型输出进入持久化前收紧不变量。

## DDD 与 Alcedo 分层

```text
api/presentation -> application -> domain
        |                ^           ^
        v                |           |
infrastructure ----------+-----------+
        |
ai adapters / external providers
```

| 层 | 当前职责 | 关键路径 |
|---|---|---|
| Presentation/API | HTTP 路由、请求 scope、状态码和错误映射 | [`backend/cinecontext/api`](../backend/cinecontext/api) |
| Application | parse/run 用例、端口、事务边界、错误语义 | [`backend/cinecontext/application`](../backend/cinecontext/application) |
| Domain | `Film`、`CurationRun`、证据/时间线实体、run 状态流、仓储 Protocol | [`backend/cinecontext/domain`](../backend/cinecontext/domain) |
| Infrastructure | SQLAlchemy 仓储、scoped persistence、DI 注册 | [`backend/cinecontext/infrastructure`](../backend/cinecontext/infrastructure) |
| AI | 模型端口/适配器、工作流节点、研究 provider、输出校验 | [`backend/cinecontext/ai`](../backend/cinecontext/ai) |
| Framework | 配置、DI、UoW、repository 基类、FastAPI 集成 | [`backend/alcedo`](../backend/alcedo) |

`backend/alcedo` 是随仓库交付的 Alcedo。应用代码应从公开 `alcedo` exports 引入可用能力，而不是依赖框架内部模块路径；公开面定义于 [`backend/alcedo/__init__.py`](../backend/alcedo/__init__.py)。

依赖方向不是“所有代码只能向下 import”的机械规则。例如基础设施实现领域仓储端口，AI 使用 application persistence 端口。但核心约束是：领域和应用协议不依赖 SQLAlchemy 仓储实现、OpenAI SDK 或具体搜索服务；具体适配器在外层实现协议并由组合根装配。

## 组合根与生命周期

存在两个应用组合根：

| 模式 | 组合根 | 注入差异 |
|---|---|---|
| production | [`cinecontext/main.py`](../backend/cinecontext/main.py) | `OpenAIModelProvider`，要求 `model.mode=openai` 和有效模型配置 |
| mock | [`cinecontext/mock/main.py`](../backend/cinecontext/mock/main.py) | `MockModelProvider`、demo repository、启动时幂等 seed |

共同的 [`bootstrap.py`](../backend/cinecontext/bootstrap.py) 创建 FastAPI，解析数据库 URL，调用 `configure_alcedo`，注册共同服务和 `/api` router，并在存在 `frontend/dist` 时把它挂载到 `/`。Alcedo 为每个 HTTP 请求创建 scope；后台 `CurationJob` 在原请求 scope 结束后再创建独立 scope。

应用 lifespan 启动时执行 `StartupService.initialize()`：将数据库中仍为 `queued` 或 `running` 的 run 标记为 `interrupted`。mock 的 `MockStartupService` 还会 seed 演示电影、来源、passage 与评分。

## AI 状态机

[`workflow.py`](../backend/cinecontext/ai/workflow.py) 定义 `WorkflowContext`、`WorkflowNode`、`WorkflowTransition`、`TransitionEngine` 和 `WorkflowRunner`；[`nodes.py`](../backend/cinecontext/ai/nodes.py) 实现无会话状态的节点并声明完整 transition table。

```text
PLAN -> DISCOVER -> ASSESS
                     | follow_up
                     v
                  FOLLOW_UP --success--> ASSESS
                     |
ASSESS --success--> SELECT -> TIMELINE -> GUIDE --retry--> GUIDE
                     |                      |
                     | partial              v
                     +-------------->     RATINGS
                                            |
                          +-----------------+----------------+
                          v                                  v
                       PUBLISH                      PUBLISH_PARTIAL
                          +---------------+------------------+
                                          v
                                      COMPLETED

未处理异常 -> CurationService -> 持久化 FAILED
```

context 是单次 run 的可变工作内存，包含约束、计划、候选、证据、决策、入选电影、timeline/guide 和有界重试计数。runner 要求节点返回同一个 context、设置 outcome，且 transition 必须恰好匹配一条；总步数上限为 32。更详细的不变量见 [AI 工作流](ai-workflow.md)。

## 模型 Provider 架构

[`ai/ports.py`](../backend/cinecontext/ai/ports.py) 定义 `ApplicationModel` 和工厂式 `ApplicationModelProvider`。每个 run 调用 `provider.create()` 获得独立模型对象，结束后 `close()`。

- production 的 [`openai_model.py`](../backend/cinecontext/ai/openai_model.py) 使用 `AsyncOpenAI`，支持 `chat_completions` 与 `responses` 两种 API style，并将返回 JSON 校验为 contracts。
- mock 的 [`mock/ai/model.py`](../backend/cinecontext/mock/ai/model.py) 生成确定性结果，用于演示和 smoke test。
- provider 暴露 `mode`，健康检查和 run 数据据此区分 `openai`/`mock`。

## Research Tools Provider 架构

[`research_tools/main.py`](../backend/cinecontext/ai/research_tools/main.py) 是该子系统的公共组装点，明确包含三层：

| 构件 | 作用 |
|---|---|
| `FilmMetadataProvider`、`WebSearchProvider`、`RatingsProvider`、`ResearchTools` | 面向调用方的 Protocol 接口 |
| `ResearchToolsFacade` | 把 local、metadata、web、ratings 合并成工作流只需依赖的统一 facade，并暴露 enabled 状态 |
| `create_research_tools(...)` | 工厂；共享 persistence/http/options，构造具体 provider，并实施 Brave 优先选择 |

各 provider 文件保持单一外部系统职责：

| 文件 | 所有权 |
|---|---|
| [`local.py`](../backend/cinecontext/ai/research_tools/local.py) | 本地候选、电影 projection 和 PostgreSQL passage 搜索 |
| [`tmdb.py`](../backend/cinecontext/ai/research_tools/tmdb.py) | TMDB 精确电影匹配、metadata 入库、TMDB audience rating |
| [`brave.py`](../backend/cinecontext/ai/research_tools/brave.py) | Brave LLM Context excerpts |
| [`duckduckgo.py`](../backend/cinecontext/ai/research_tools/duckduckgo.py) | DuckDuckGo HTML 结果解析；可抓正文，失败时保留 snippet |
| [`crawler.py`](../backend/cinecontext/ai/research_tools/crawler.py) | 公网 URL/DNS 校验、重定向/大小/content-type 限制、可见文本提取 |
| [`omdb.py`](../backend/cinecontext/ai/research_tools/omdb.py) | 按 IMDb ID 获取 IMDb/Rotten Tomatoes/Metacritic 快照 |
| [`ratings.py`](../backend/cinecontext/ai/research_tools/ratings.py) | 为评分增加 provider/status/as_of 并统一持久化 |

有 Brave key 时工厂只选择 Brave web provider；否则选择 DuckDuckGo + crawler。TMDB 和 OMDb 按各自凭据独立启用，不影响本地检索。

## 事务与持久化边界

Alcedo 的 `@transactional` 调用对象公开的 `uow.execute()`；同一 UoW 中的嵌套调用加入当前事务。HTTP 应用服务的创建、删除、启动清理是明确事务。

长时间 AI run 使用另一种边界：singleton `ScopedCurationPersistence` 的每个方法新建 Alcedo scope，解析 scoped `CurationPersistenceService`，执行一次短操作后关闭 scope。写操作由 `@transactional` 包裹，读操作在短 scope 中完成。因此模型请求、TMDB/搜索/crawler/OMDb 请求不会持有数据库事务或行锁。

典型序列是：外部调用 -> 审计短事务 -> 证据短事务 -> 下一外部调用。代价是整个研究不是一个原子事务；收益是长 I/O 不占用事务，且中间进度和证据可观察。异常由 `CurationService` 转换为 terminal `FAILED` 写入。

## 数据流

```text
prompt -> POST /requests/parse -> structured Constraints
Constraints + submission_key -> POST /runs -> queued CurationRun
BackgroundTasks -> CurationJob -> CurationService
model plan -> local/TMDB discovery -> local/web evidence
-> model assessment -> bounded follow-up -> selection
-> timeline model + deterministic validation
-> guide model + citation validation -> ratings
-> persisted completed/partial result -> polling GET /runs/{id}
```

`submission_key` 在 `runs` 表唯一；相同 key 和相同 constraints 返回已有 run，不重复调度，constraints 不同则返回 conflict。API 只在 terminal 状态返回 `study`，避免客户端读取仍在变化的研究结果。

## 数据库所有权

| 数据 | 主要表 | 写入者 |
|---|---|---|
| 电影目录、上映记录 | `films`、`film_releases` | mock seed 或 TMDB provider 经 FilmRepository |
| 长期来源语料 | `sources`、`passages`、`film_passages` | mock seed；本地全文检索只读 |
| run 聚合 | `runs` | RunApplicationService / CurationPersistenceService |
| run 候选与证据快照 | `run_films`、`run_evidence` | workflow persistence |
| 时间线关系 | `timeline_events`、`film_events`、`timeline_edges` | TimelineNode 经 ResearchRepository |
| 工具审计 | `tool_calls` | `WorkflowOperations.logged` |
| 可更新评分快照 | `rating_snapshots` | TMDB/OMDb 或 mock seed |

schema 由 [`0001_initial.py`](../backend/migrations/versions/0001_initial.py) 所有，包含 PostgreSQL JSONB、TSVECTOR、GIN、复合外键和唯一约束。应用设置 `database.create_schema: false`，正常建表路径是 Alembic，而不是 ORM `create_all`。

## 重要不变量与取舍

- `CurationRun` 的状态变化由领域 `StateFlow` 限制；启动时中断遗留 active run，不自动续跑。
- accepted 决策必须引用该 film 的非 metadata evidence；公共 evidence 可用于多个 film 的指南 claim。
- guide 必须恰好覆盖所有 selected film，且 `viewing_order` 也必须各出现一次。
- research timeline 事件只能引用 selected film 和已发布 evidence；release timeline 由目录 metadata 确定性生成。
- follow-up 最多 4 个不同 `(film, query)`，每部 film 最多 2 次；guide 最多 2 次；runner 最多 32 步。
- run evidence 是快照，保证结果引用不随长期 passage 变化；代价是存储重复。
- ratings 在 guide 后运行；评分失败属于可降级限制，不回滚已完成研究。
- 后台任务绑定 Web 进程，没有 durable queue；进程重启后 active run 变为 interrupted。
- initial schema 没有 `ON DELETE CASCADE`，删除 terminal run 时 repository 按依赖顺序显式删除 run-owned 子表；film、source、passage 和 rating 不属于单个 run，不随之删除。
