# 开发指南与 API

## 本地开发约定

- 从仓库根目录创建 `.venv`，以 editable 模式安装 `backend`；测试才能直接 import `cinecontext` 和 vendored `alcedo`。
- 应用代码使用公开 `alcedo` 框架 exports，例如 `from alcedo import UnitOfWork, transactional`。公开项见 [`backend/alcedo/__init__.py`](../backend/alcedo/__init__.py)。框架项目链接见 [https://github.com/CheesePanzer/AlcedoButPython](https://github.com/CheesePanzer/AlcedoButPython)。`alcedo` 框架提供实现DDD的相关工具，框架设计上有借鉴 Asp.NET Core的相关功能。涉及`alcedo`底层的修改必须要求人工审查确认。
- API controller 保持薄：请求解析、DI 和 HTTP shape 留在 `api/`，用例/事务在 `application/`，数据库细节在 `infrastructure/`。
- 新外部服务通过 Protocol + adapter + composition root 接入，不直接从 domain 调 SDK。
- 新状态、evidence kind、operation name 等稳定值优先使用 `StrEnum`，同时为 JSON value 补回归测试。

前端开发服务器可独立启动：

```bash
npm --prefix frontend run dev
```

一体化脚本不运行 Vite dev server，而是每次构建 `frontend/dist` 并由 FastAPI 提供。前端 API 使用相对 `/api` 路径；独立 Vite 开发时需自行处理后端同源/代理需求。

## API 概览

所有业务路由以 `/api` 为前缀。FastAPI 自动文档在服务运行时通常可通过 `/docs` 查看，但当静态前端挂载到 `/` 后，实际路由匹配应以 FastAPI 当前注册结果为准；下表是代码中的稳定入口。

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/api/health` | 返回 mode 与 provider enabled 状态 |
| POST | `/api/requests/parse` | 自然语言 prompt 转 constraints |
| POST | `/api/runs` | 幂等创建 run，返回 202，并在新建时调度后台任务 |
| GET | `/api/runs` | 最近 20 个 run |
| GET | `/api/runs/{run_id}` | 进度与 terminal 结果 |
| DELETE | `/api/runs/{run_id}` | 删除 terminal run 及其 run-owned 数据 |
| GET | `/api/runs/{run_id}/films/{film_id}` | 入选片目、对应 guide 与证据 |
| GET | `/api/films/{film_id}/ratings` | 电影评分快照 |

`POST /api/runs` 的 `submission_key` 是幂等键。相同 key + 相同 constraints 返回已有 run；相同 key + 不同 constraints 返回 409 conflict。

近期的 DELETE 约束是重要并发不变量：仅 `completed`、`partial`、`failed`、`interrupted` 可删除。repository 先 `SELECT ... FOR UPDATE`，active 的 `queued`/`running` 返回 409 `An active research run cannot be deleted.`，不存在返回 404。因为初始 schema 无级联删除，repository 只按顺序删除该 run 的 timeline links/events、tool calls、evidence、decisions 和 run；共享 film/source/passage/rating 保留。

## 数据库迁移

schema 变更必须新增 `backend/migrations/versions/` 下的 Alembic revision，不要修改已部署环境依赖的旧 revision。当前唯一 revision 是 `0001`。

应用默认 `database.create_schema: false`，以 Alembic 为 schema 所有者：

```bash
DATABASE_URL='postgresql+asyncpg://cinecontext:<encoded-password>@127.0.0.1:5432/cinecontext' \
  ./.venv/bin/python -m alembic -c backend/alembic.ini upgrade head
```

创建迁移前先阅读 domain mappings 和当前 SQL migration。由于 schema 使用 PostgreSQL JSONB、TSVECTOR、computed column、GIN 和复合外键，自动生成结果需要人工审查。

## 测试层次

单元/组件测试：

```bash
./.venv/bin/python -m unittest tests.test_ai_workflow
```

当前该文件覆盖 enum/JSON shape、timeline filtering、guide citation ownership、transition/runner 契约、DDG/crawler 解析与 URL 安全、provider precedence，以及 terminal run 删除限制。

端到端 smoke：

```bash
CINECONTEXT_APP=mock CINECONTEXT_PYTHON="$PWD/.venv/bin/python" ./scripts/start.sh
./.venv/bin/python tests/smoke.py
```

smoke 针对运行中的真实 API 和 PostgreSQL，覆盖三个 demo 主题及 unsupported topic。可设置 `CINECONTEXT_TEST_URL` 改目标。它不是隔离测试，会在目标数据库创建 run。

前端构建检查：

```bash
npm --prefix frontend ci
npm --prefix frontend run build
```

## 常见变更位置

| 目标 | 修改入口 |
|---|---|
| 新 HTTP 用例 | `api/controllers.py` + `application/services.py`，必要时扩展 contracts/ports |
| 新领域状态/实体 | `domain/entities.py` + repository protocol + migration + infrastructure implementation |
| 新 AI 步骤 | `ai/workflow.py`、`ai/nodes.py` 和 transition tests |
| 新模型供应商 | 实现 `ai/ports.py`，在组合根注册 provider |
| 新研究供应商 | `ai/research_tools/` 独立文件，并在 `main.py` factory 选择 |
| 新配置 | `settings.py` typed options + appsettings 默认值 + `.env.example` + 部署文档 |
| 新 demo 场景 | `mock/demo_data.py` + `mock/ai/model.py` + smoke assertions |

## 变更检查清单

1. 确认依赖方向：核心协议不 import 具体 SDK/SQLAlchemy adapter。
2. 确认外部 HTTP/model 调用不位于数据库事务内。
3. 确认新增循环有显式上限，节点所有路径设置 outcome。
4. 确认 evidence、film 和 run ownership 在写入前验证。
5. 确认 appsettings、typed setting、`.env.example` 和文档变量名一致。
6. 运行相关 unit test、mock smoke（若行为变化）、前端 build（若 UI 变化）。
7. 运行 `git diff --check`，检查迁移 upgrade/downgrade 与 API 错误行为。

## 当前测试空白

- 没有浏览器级前端自动化测试。
- 没有针对真实 OpenAI-compatible、TMDB、Brave 或 OMDb 账号的 CI integration test。
- smoke 依赖运行中的 mock 服务和 PostgreSQL，不由 unit test 自动启动。
- 后台任务是进程内 `BackgroundTasks`，当前没有多 worker/崩溃恢复的集成覆盖。
