# CineContext

CineContext 是一个有证据约束的电影研究与观影指南应用。用户用自然语言描述主题、年代、地区等条件，系统寻找候选电影、核对来源、生成带引用的观影指南，并提供上映时间、故事时期、神话和历史等时间线视图。

项目同时提供确定性的 mock 模式和连接真实模型/检索服务的 production 模式。mock 不是纯前端假数据：它仍使用 PostgreSQL、Alembic、完整状态机和持久化流程。

## 主要能力

- 将自然语言问题解析为结构化电影筛选条件。
- 结合本地全文检索、TMDB 和网页检索发现及核验候选片目。
- 强制校验入选电影的来源、引用归属和指南覆盖范围。
- 生成 release、story、myth、history 多轴时间线。
- 记录研究进度、证据快照、候选决策、工具调用与评分快照。
- 使用 submission key 保证重复提交幂等，并保留可查询的研究结果。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React 19、Vite 7 |
| Web/API | FastAPI、Uvicorn |
| 应用框架 | 仓库内置 Alcedo（DI、配置、UoW、错误映射） |
| 数据 | PostgreSQL、SQLAlchemy Async、asyncpg、Alembic |
| AI | OpenAI Python SDK，支持 OpenAI-compatible Chat Completions / Responses |
| 外部研究 | TMDB、Brave LLM Context、DuckDuckGo HTML + crawler、OMDb |

## 5 分钟启动

前置要求：Python 3.14、Node.js/npm、PostgreSQL 命令 `initdb` 和 `pg_ctl`。详细安装、Docker 数据库和生产配置见[部署文档](docs/deployment.md)。

### Windows PowerShell（mock）

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .\backend
.\scripts\start.ps1
```

若当前进程禁止执行脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

### Bash（mock）

`start.sh` 默认从名为 `cinecontext` 的 Conda 环境寻找 Python；也可显式指定解释器：

```bash
python3.14 -m venv .venv
./.venv/bin/python -m pip install -e backend
CINECONTEXT_PYTHON="$PWD/.venv/bin/python" ./scripts/start.sh
```

启动器会安装/校验依赖、执行 Alembic、运行 `npm ci`、构建 `frontend/dist`，再由 FastAPI 在 `http://127.0.0.1:8765` 提供 API 和静态前端。未设置 `DATABASE_URL` 时，它会在 `.runtime/` 初始化本机 PostgreSQL 实例。

生产模式需要模型凭据，配置示例和所有变量见[部署文档](docs/deployment.md)：

```bash
CINECONTEXT_APP=production APP__MODEL__API_KEY='<key>' \
APP__MODEL__MODEL_ID='<model>' ./scripts/start.sh
```

运行日志默认写入 `.runtime/logs/cinecontext.log`；配置、轮转和按 `run_id`/diagnostic ID 排障见 [部署文档](docs/deployment.md#日志与故障关联)。

## 架构概览

```text
React / HTTP
     |
FastAPI controllers + Alcedo request scope
     |
Application services / transaction boundaries
     |
Domain entities + repository/AI ports
     |
+----------------------+-------------------------+
| AI workflow          | Infrastructure          |
| nodes -> transitions | SQLAlchemy/PostgreSQL   |
| model + research     | scoped persistence/UoW  |
+----------------------+-------------------------+
        |                         |
 OpenAI-compatible       TMDB / web / OMDb
```

依赖方向以领域和应用协议为中心。`backend/cinecontext/main.py` 与 `backend/cinecontext/mock/main.py` 是 production/mock 组合根；`backend/cinecontext/bootstrap.py` 组装共同的 FastAPI、Alcedo、数据库和静态资源。完整说明见[系统架构](docs/architecture.md)。

## 仓库结构

```text
.
├── backend/
│   ├── alcedo/                  # 内置框架：DI、配置、UoW、Web 集成
│   ├── cinecontext/
│   │   ├── api/                 # 薄 FastAPI 控制器
│   │   ├── application/         # 用例、端口、事务边界
│   │   ├── domain/              # 实体、运行状态、仓储协议
│   │   ├── infrastructure/      # SQLAlchemy 仓储与持久化适配器
│   │   ├── ai/                  # 模型端口、工作流、校验、研究工具
│   │   └── mock/                # 确定性模型、演示数据、mock 组合根
│   ├── migrations/              # Alembic 迁移
│   ├── appsettings.yaml         # production 默认配置
│   └── appsettings.mock.yaml    # mock 默认配置
├── frontend/                    # React 单页应用；构建结果为 dist/
├── scripts/                     # Windows/Bash 一体化启动器
├── tests/                       # 单元测试和运行中服务的 smoke 测试
└── docs/                        # 面向维护者和部署者的文档
```

`backend/alcedo` 是 vendored Alcedo 实现。应用代码应从公开的 `alcedo` exports 消费框架能力，例如 `from alcedo import UnitOfWork`。

## 文档索引

| 文档 | 内容 |
|---|---|
| [系统架构](docs/architecture.md) | 设计原则、DDD/Alcedo 分层、组合根、事务、provider 与数据流 |
| [部署与配置](docs/deployment.md) | 环境准备、PostgreSQL、全部配置、启动、健康检查和排错 |
| [AI 工作流](docs/ai-workflow.md) | 状态图、节点/转移契约、校验、审计和扩展方式 |
| [工具调用与 Function Calling](docs/function-calling.md) | 当前工具清单、调用时机、Provider、审计，以及与原生 Function Calling的区别 |
| [开发指南](docs/development.md) | 目录约定、API、迁移、测试及变更检查清单 |

## 测试

安装后端为 editable package 后，在仓库根目录运行单元测试：

```bash
python -m unittest tests.test_ai_workflow
```

启动 mock 服务后运行端到端 smoke：

```bash
python tests/smoke.py
```

可用 `CINECONTEXT_TEST_URL` 指向非默认地址。前端当前没有独立测试套件，可用构建检查验证：

```bash
npm --prefix frontend run build
```

## 当前状态与限制

- 当前版本为 `0.1.0`，单进程 FastAPI `BackgroundTasks` 执行研究任务，不是分布式任务队列。
- mock 数据集覆盖 Norse mythology、state surveillance、Romanian political memory 三组演示主题，共 11 部电影；其他主题可能只得到部分或空结果。
- production 输出指南为英文；来源摘录保留原始语言。
- 免费 DuckDuckGo/crawler 路径是 best effort，不渲染 JavaScript，且可能被限流、站点策略或 `robots.txt` 限制。
- PostgreSQL 是当前 schema 和全文检索能力的目标数据库；迁移包含 PostgreSQL `JSONB`、`TSVECTOR` 与 GIN 索引。
- 启动时会把上次仍为 queued/running 的任务标记为 interrupted；任务不会自动恢复。
