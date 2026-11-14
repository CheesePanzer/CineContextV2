# 工具调用与 Function Calling

本文说明 CineContext 当前有哪些可被 AI 工作流使用的工具，以及它们与 OpenAI 原生 Function Calling 的区别。AI 状态机本身见 [AI 工作流](ai-workflow.md)，provider 的实现边界见 [系统架构](architecture.md)。

## 当前结论

CineContext 当前**没有**把函数 schema 传给 OpenAI SDK，也没有使用 `tools=`、`tool_choice`、tool call message 或“模型自主选择函数”的原生 Function Calling。

当前采用的是**应用编排的工具调用**：

```text
WorkflowRunner 根据 state 找到节点
    -> 节点根据确定性业务规则调用 ResearchTools
    -> ResearchTools facade 委托具体 provider
    -> 结果转成 typed record
    -> 节点更新 WorkflowContext
    -> TransitionEngine 决定下一状态
```

模型负责 `plan`、`assess`、`timeline` 和 `guide` 等结构化推理；应用负责决定什么时候查数据库、什么时候搜索网页、什么时候查询 metadata 和 ratings。模型不能绕过状态机直接发起任意网络请求。

## 为什么采用应用编排

| 目标 | 当前设计的处理方式 |
|---|---|
| 可预测执行 | 节点和 transition table 明确规定允许的调用顺序 |
| 有界循环 | follow-up 总数、单片次数、guide retry 和 runner steps 均有上限 |
| 引用可信 | 搜索结果先持久化为 evidence，模型只能返回已存在的 evidence ID |
| 事务安全 | 外部调用完成后才进入短 persistence/UoW，不在数据库事务内等待网络 |
| Provider 可替换 | 工作流只依赖 `ResearchTools` Protocol，具体服务由 factory 选择 |
| 可审计 | 每次工具调用记录 operation、status、elapsed time 和 detail |

这种设计牺牲了开放式 agent 自主调用工具的灵活性，但更适合当前“检索、核验、生成引用指南”的固定业务流程。

## 统一工具接口

公共接口位于 [`backend/cinecontext/ai/research_tools/main.py`](../backend/cinecontext/ai/research_tools/main.py)：

```python
class ResearchTools(Protocol):
    async def find_candidates(self, theme: str) -> Sequence[UUID]: ...
    async def lookup_local_movie(self, film_id: UUID) -> FilmProjection: ...
    async def lookup_movie(self, hint: FilmHint) -> FilmProjection | None: ...
    async def search_passages(
        self, query: str, film_id: UUID | None = None
    ) -> list[EvidenceDraft]: ...
    async def search_web(
        self, query: str, film_id: UUID | None = None
    ) -> list[EvidenceDraft]: ...
    async def lookup_ratings(self, film: FilmProjection) -> list[Rating]: ...
```

`ResearchToolsFacade` 实现该统一接口，`create_research_tools(...)` 负责组装具体 provider。节点只看到这个接口，不知道 Brave、DuckDuckGo、TMDB 或 OMDb 的协议细节。

## 现有工具清单

### `find_candidates`

| 项目 | 内容 |
|---|---|
| 用途 | 按结构化主题从本地电影目录查找候选 ID |
| 调用节点 | `DiscoverNode` |
| 输入 | `theme: str` |
| 输出 | `Sequence[UUID]` |
| 实现 | `LocalResearchProvider` |
| 数据源 | PostgreSQL `films.facts` |
| 审计 operation | `find_candidates` |

### `lookup_local_movie`

| 项目 | 内容 |
|---|---|
| 用途 | 加载电影及上映记录，构造成 AI 工作流使用的 `FilmProjection` |
| 调用节点 | `DiscoverNode` |
| 输入 | `film_id: UUID` |
| 输出 | `FilmProjection` |
| 实现 | `LocalResearchProvider` |
| 数据源 | PostgreSQL `films`、`film_releases` |
| 审计 operation | `lookup_local_movie` |

### `lookup_movie`

| 项目 | 内容 |
|---|---|
| 用途 | 根据模型计划中的标题/年份 hint 补充外部电影 metadata |
| 调用节点 | `DiscoverNode`，仅 TMDB启用时 |
| 输入 | `FilmHint` |
| 输出 | `FilmProjection | None` |
| 实现 | `TmdbMetadataProvider` |
| 数据源 | TMDB API；验证后写入本地 film/release/rating |
| 审计 operation | `lookup_movie` |

TMDB匹配要求标题和可选年份得到唯一候选，避免把模糊搜索结果直接作为电影事实。

### `search_passages`

| 项目 | 内容 |
|---|---|
| 用途 | 搜索本地可信来源和电影 passage |
| 调用节点 | `DiscoverNode`、`FollowUpNode` |
| 输入 | query，以及可选 `film_id` |
| 输出 | `list[EvidenceDraft]` |
| 实现 | `LocalResearchProvider` |
| 数据源 | PostgreSQL全文检索：TSVECTOR、GIN、`websearch_to_tsquery` |
| 审计 operation | `search_passages`、`search_passages.context`、`search_passages.follow_up` |

`film_id`存在时搜索该电影关联 passage；没有时搜索公共 context passage。

### `search_web`

| 项目 | 内容 |
|---|---|
| 用途 | 本地来源不足时发现并提取网页证据 |
| 调用节点 | `DiscoverNode`、`FollowUpNode` |
| 输入 | query，以及可选 `film_id` |
| 输出 | `list[EvidenceDraft]` |
| Provider优先级 | 有 Brave key 时使用 Brave，否则使用 DuckDuckGo + crawler |
| 审计 operation | `search_web`、`search_web.context`、`search_web.follow_up` |

Brave返回 LLM Context摘要。免费路径使用 DuckDuckGo HTML发现 URL，再由 crawler抓取公开 HTML/text页面；抓取失败时可降级为搜索结果 snippet。

Crawler安全边界包括：只允许 HTTP(S)、拒绝 URL凭据和本地/私有地址、逐跳校验重定向、限制 content type/字节数/页面数，不执行 JavaScript。

### `lookup_ratings`

| 项目 | 内容 |
|---|---|
| 用途 | 在主要研究完成后刷新补充评分 |
| 调用节点 | `RatingsNode` |
| 输入 | `FilmProjection`，要求 IMDb ID |
| 输出 | `list[Rating]` |
| 实现 | `OmdbRatingsProvider` |
| 数据源 | OMDb 返回的 IMDb、Rotten Tomatoes、Metacritic数据 |
| 审计 operation | `lookup_ratings` |

评分是可降级信息，不决定电影是否入选。TMDB audience score在 `lookup_movie` 过程中由 TMDB provider一并保存。

## 工具在状态机中的位置

```text
PLAN
  model.plan
    |
DISCOVER
  find_candidates
  lookup_local_movie
  lookup_movie          [可选 TMDB]
  search_passages
  search_web            [本地证据为空时]
    |
ASSESS
  model.assess
    |
FOLLOW_UP
  search_passages 或 search_web
    |
ASSESS
    |
TIMELINE / GUIDE
  model.timeline / model.generate
    |
RATINGS
  lookup_ratings        [可选 OMDb]
```

工具不是任意时刻都可调用。例如 ratings只能在 `RATINGS`节点执行，网页补证只能通过 `DISCOVER`或`FOLLOW_UP`节点执行。

## 模型调用不是 Research Tool

以下能力由 `ApplicationModel`提供，不属于 `ResearchTools`：

| 模型方法 | 用途 |
|---|---|
| `parse` | 把用户问题解析为 `Constraints` |
| `plan` | 生成搜索 query 和电影 hints |
| `assess` | 根据电影、约束和 evidence做结构化决策 |
| `timeline` | 生成有 evidence ID约束的 story/myth/history事件 |
| `generate` | 生成有 evidence ID引用的观影指南 |

生产实现通过 OpenAI-compatible SDK获得结构化 JSON；mock实现返回确定性结果。两者都不会在模型内部调用 Research Tools。

## 审计与错误降级

`WorkflowOperations.logged` 为模型和工具动作记录：

```text
operation
status: ok | empty | failed
elapsed_ms
detail
```

稳定 operation名定义于 `OperationName` enum，并写入 `tool_calls`表。可选工具发生 `httpx.HTTPError`、`ValueError`或`KeyError`时，`optional`记录失败并添加 limitation；核心模型步骤或未列入降级范围的异常会使 run失败。

工具返回的 `EvidenceDraft`不会立即交给模型。工作流先调用 persistence写入 `run_evidence`，获得数据库 UUID后转为 `EvidenceRecord`，模型随后只能引用这些 ID。

## 如果未来启用原生 Function Calling

原生 Function Calling应作为新的模型适配层能力，而不是直接暴露所有 Python函数。至少需要：

1. 为允许的工具定义稳定 schema，并映射到 typed命令。
2. 建立 tool name到 handler的白名单，拒绝模型生成的任意函数名。
3. 对每次调用执行参数校验、权限检查、超时、审计和结果大小限制。
4. 保持 crawler SSRF限制和数据库短事务边界。
5. 为 agent循环增加最大 tool calls、每工具次数和总耗时上限。
6. 将工具结果先持久化为 evidence，再允许后续模型引用。
7. 明确哪些状态允许哪些工具，避免原生 tool calling绕过现有 transition规则。

在这些约束实现前，不应把 `ResearchToolsFacade`中的方法直接转换成无边界的模型工具列表。
