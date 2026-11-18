# 部署、配置与运行

本文覆盖当前仓库自带的本地启动方式。服务默认只监听 `127.0.0.1`，脚本不是包含 TLS、反向代理、多 worker 或进程监管的完整公网部署方案。

## 前置要求

- Python 3.14（`backend/pyproject.toml` 要求 `>=3.14`）。
- Node.js 与 npm；前端 lockfile 由 `npm ci` 使用。
- PostgreSQL。自动本地实例要求 `initdb`、`pg_ctl` 可执行；Windows 脚本也会搜索 `Program Files\PostgreSQL\*\bin`。
- Docker 可选，适合提供独立 PostgreSQL；启动脚本本身不会启动 Docker。

## 根目录虚拟环境与依赖

Windows PowerShell：

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .\backend
npm --prefix .\frontend ci
```

Bash：

```bash
python3.14 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e backend
npm --prefix frontend ci
```

`start.ps1` 优先使用 `CINECONTEXT_PYTHON`，其次使用根 `.venv\Scripts\python.exe`，最后使用 PATH 中的 `python`。`start.sh` 优先使用 `CINECONTEXT_PYTHON`；未设置时执行 `conda run -n cinecontext ...`，因此 Bash 使用 venv 时应显式设置该变量。

## PostgreSQL：Docker 方式

以下示例创建容器中的管理员实例，再显式创建应用 role/database。所有密码均为占位符，不要提交实际值。

```bash
docker run --name cinecontext-postgres \
  -e POSTGRES_PASSWORD='<admin-password>' \
  -p 5432:5432 -d postgres:17

docker exec -it cinecontext-postgres psql -U postgres \
  -c "CREATE ROLE cinecontext LOGIN PASSWORD '<app-password>';"
docker exec -it cinecontext-postgres psql -U postgres \
  -c "CREATE DATABASE cinecontext OWNER cinecontext;"
```

PowerShell 可把续行符换成反引号，或将每条命令写成一行。应用连接串使用 asyncpg dialect：

```text
DATABASE_URL=postgresql+asyncpg://cinecontext:<encoded-app-password>@127.0.0.1:5432/cinecontext
```

如果用户名或密码含 `@`、`:`、`/`、`?`、`#`、`%` 等 URL 保留字符，必须做 percent encoding。例如原密码 `p@ss:word` 在 URL 中写作 `p%40ss%3Aword`。可以只对用户名/密码组件编码，不要对整个 URL 编码：

```bash
python -c "from urllib.parse import quote; print(quote('<app-password>', safe=''))"
```

## PostgreSQL：脚本自动实例

只有在 `DATABASE_URL` 缺失或为空时，`start.ps1`/`start.sh` 才会在 `.runtime/postgres` 初始化本机实例，生成 `.runtime/db-password`，并默认监听 `127.0.0.1:55438`。可用 `CINECONTEXT_DB_PORT` 改端口。

特别是 `start.ps1`：一旦 `.env` 或当前环境提供 `DATABASE_URL`，它不会自动查找、初始化或启动本地 PostgreSQL，而会直接使用该 URL。自动实例的 database 是 PostgreSQL 默认的 `postgres`，role 是 `cinecontext`；这与上面的 Docker 自建 `cinecontext` database 示例不同，均为有效配置。

## Alembic

启动脚本每次都会先执行 `alembic upgrade head`。手工执行时必须提供 `DATABASE_URL`，并从 `backend` 工作目录运行：

PowerShell：

```powershell
$env:DATABASE_URL = 'postgresql+asyncpg://cinecontext:<encoded-password>@127.0.0.1:5432/cinecontext'
.\.venv\Scripts\python.exe -m alembic -c .\backend\alembic.ini upgrade head
```

Bash：

```bash
DATABASE_URL='postgresql+asyncpg://cinecontext:<encoded-password>@127.0.0.1:5432/cinecontext' \
  ./.venv/bin/python -m alembic -c backend/alembic.ini upgrade head
```

若直接在 `backend/` 中运行，命令是 `python -m alembic upgrade head`。当前迁移使用 PostgreSQL 专属类型和全文检索结构。

## 启动

Windows：

```powershell
.\scripts\start.ps1
```

Bash：

```bash
CINECONTEXT_PYTHON="$PWD/.venv/bin/python" ./scripts/start.sh
```

两个启动脚本都会：读取根 `.env`、检查/按需安装 editable 后端、准备或使用 PostgreSQL、执行 Alembic、运行 `npm ci`、构建 `frontend/dist`，最后启动 Uvicorn。它们不是 Vite dev server；FastAPI 的 `bootstrap.py` 直接提供构建后的静态 `dist` 和 SPA fallback。

默认是 mock：

```bash
CINECONTEXT_APP=mock ./scripts/start.sh
```

production：

```bash
CINECONTEXT_APP=production \
APP__MODEL__API_KEY='<api-key>' \
APP__MODEL__MODEL_ID='<model-id>' \
./scripts/start.sh
```

## TACC Qwen

完成课程 `tcloud` 账号配置后，可以用 `./scripts/start.sh --submit-tacc` 提交新作业并启动本地应用。该命令记录作业编号、提交时间和时限。使用 TACC 配置时，`./scripts/start.sh` 优先读取显式配置的连接，否则恢复上次提交的作业。`./scripts/start.sh JOB_ID` 可以指定已有作业。

```bash
(cd deploy/tacc && tcloud submit)
tcloud ps -j JOB_ID
tcloud cat slurm_log/cinecontext-qwen-JOB_ID.log
./scripts/start.sh JOB_ID
```

GPU 作业申请两张卡，最长运行一小时。网页会显示排队、加载和就绪状态，日志出现 `CINECONTEXT_QWEN_READY` 后即可提交研究。同一个作业可以处理多条请求。演示结束后可执行 `tcloud cancel -j JOB_ID`。下次启动模型后，使用新的作业编号。

作业停止时，`service_supervisor.py` 会关闭队列 worker 和 vLLM 的进程组。Slurm 日志中的 `CINECONTEXT_CLEANUP` 标记记录停止信号，之后会打印 GPU 剩余显存。单次推理超时返回 HTTP 504，作业仍可处理后续请求。

网页约每 15 秒查询一次状态。排队时显示从提交起已等待多久。分到 GPU 后，剩余时间按申请时限减去 Slurm 报告的运行时长计算，包含模型加载时间。手动提交且没有本地提交记录的作业只显示状态。

若 `tcloud ps -j JOB_ID` 显示 `PD (Resources)`，作业还在等 GPU。若作业刚启动就退出，检查 Slurm 日志里的显存检查结果。遇到明确的显存不足时，可以在提交配置的 `job.general` 加上 `exclude=NODE_NAME`，再提交并使用新编号。

在本机网页中，作业结束后可以从左侧点击 **Request TACC GPUs**。申请成功后页面会跟踪新作业，重新启动服务也会读取新编号。本机 `.env` 可用 `CINECONTEXT_TACC_EXCLUDE_NODES` 为此按钮设置逗号分隔的节点排除列表。

作业仍在排队、加载或运行时，可以点击 **Restart · 1 hour** 提前更换作业。确认后，后端取消当前作业，确认停止后提交新的申请。正在处理的模型请求会被中断，新作业需要重新排队和加载 Qwen。重复点击同一旧作业只会得到同一个新作业编号。取消无法确认时，刷新状态后再试。

PowerShell 或固定本机配置时，在 `.env` 设置以下字段，再运行正常的启动脚本。

```dotenv
CINECONTEXT_APP=production
APP_ENVIRONMENT=tacc
APP__MODEL__TACC_JOB_ID=12345
APP__MODEL__TCLOUD_BIN=tcloud
APP__TOOLS__TMDB__ACCESS_TOKEN=
```

`TCLOUD_BIN` 可填 SDK 的绝对路径。FastAPI 通过 SDK 在提交账号的个人目录交换请求文件，vLLM 在 GPU 作业内监听本地端口。若后端可访问课程的 HTTP 入口，可以用以 `/v1` 结尾的 `APP__MODEL__BASE_URL` 替换作业编号，并按入口要求配置密钥。TACC 配置默认使用 Wikipedia 检索，已配置的 Brave 服务优先。TACC 使用数据库中已有的馆藏。需要加载项目自带的演示资料时，在演示数据库上先运行 mock 入口。与旧版同时运行时，可为 V2 设置网页端口 `8766` 和 PostgreSQL 端口 `55439`。

只有运行 FastAPI 的机器需要配置 SDK。若组员各自在自己的机器运行 FastAPI，需要使用各自有权限的 TACC 账户和作业。通过同一个已部署的 FastAPI 页面访问时，浏览器端沿用该后端的模型连接。

## DeepSeek / OpenAI-compatible 示例

Chat Completions 风格的 DeepSeek 示例（model ID 应以供应商当前账号可用值为准）：

```dotenv
CINECONTEXT_APP=production
APP__MODEL__API_STYLE=chat_completions
APP__MODEL__BASE_URL=https://api.deepseek.com
APP__MODEL__API_KEY=<api-key>
APP__MODEL__MODEL_ID=<deepseek-model-id>
APP__MODEL__TIMEOUT_SECONDS=180
APP__MODEL__MAX_TOKENS=6000
APP__MODEL__TEMPERATURE=0.2
APP__MODEL__JSON_RESPONSE=true
APP__MODEL__THINKING=disabled
```

对于实现 OpenAI Responses API 的 endpoint，设置 `APP__MODEL__API_STYLE=responses`。Responses 路径使用 `prompt_cache_key`（仅当 `APP__MODEL__PROMPT_CACHE=true`），不使用 chat 路径的 `MAX_TOKENS`、`TEMPERATURE`、`JSON_RESPONSE` 或 `THINKING` 请求参数。

## 配置来源与优先级

启动器先继承当前进程环境，再读取根 `.env`。当前脚本的 `.env` 值会覆盖同名进程环境值；不要依赖“shell 一定覆盖 `.env`”。Bash 通过 `source .env`，PowerShell 解析非空非注释的 `NAME=VALUE` 行，二者都不执行变量插值的一致性保证，因此 `.env` 应保持简单赋值。

应用配置的合并顺序由低到高为：

1. production 的 `backend/appsettings.yaml` 或 mock 的 `backend/appsettings.mock.yaml`。
2. 若设置 `APP_ENVIRONMENT=<name>`，同目录同 stem 的 `appsettings.<name>.yaml`（存在时）。
3. `APP__...` 环境变量，双下划线表示嵌套 key。

数据库 URL 在共同 bootstrap 中的优先级是显式 `create_app(database_url=...)` 参数、`DATABASE_URL`、合并后的 `database.url`。脚本通过环境变量提供它。`CINECONTEXT_APP` 选择组合根。mock 组合根使用 `mock`，production 支持 `openai` 和 `tacc`。

## 全部相关环境变量

### 启动器与数据库

| 变量 | 默认值/作用 |
|---|---|
| `CINECONTEXT_APP` | `mock`；可选 `mock`、`production` |
| `CINECONTEXT_PYTHON` | Python 可执行文件；覆盖脚本自动选择 |
| `CINECONTEXT_PORT` | `8765`；Uvicorn 本机端口 |
| `CINECONTEXT_DB_PORT` | `55438`；仅自动本地 PostgreSQL 使用 |
| `DATABASE_URL` | SQLAlchemy async URL；缺失时脚本自动准备本机 PostgreSQL |
| `CINECONTEXT_TEST_URL` | smoke test 的服务根地址，默认 `http://127.0.0.1:8765` |
| `APP_ENVIRONMENT` | 可选 appsettings 环境后缀，课程模型使用 `tacc` |

### 模型 `APP__MODEL__*`

| 变量 | 默认值/约束 |
|---|---|
| `APP__MODEL__MODE` | production 默认 `openai`，TACC 配置文件使用 `tacc` |
| `APP__MODEL__API_STYLE` | `chat_completions`；或 `responses` |
| `APP__MODEL__BASE_URL` | 可选 OpenAI-compatible base URL |
| `APP__MODEL__API_KEY` | `openai` 模式必填，TACC HTTP 入口按课程配置填写 |
| `APP__MODEL__MODEL_ID` | `openai` 模式必填，TACC 默认 `qwen3.8-27b` |
| `APP__MODEL__TACC_JOB_ID` | SDK 方式使用的课程作业编号 |
| `APP__MODEL__TCLOUD_BIN` | 默认 `tcloud`，可填可执行文件的绝对路径 |
| `APP__MODEL__TIMEOUT_SECONDS` | 默认 `60`，TACC 配置为 `420` |
| `APP__MODEL__MAX_RETRIES` | `1`，OpenAI SDK retry 次数，至少 0 |
| `APP__MODEL__MAX_TOKENS` | 可选正整数；chat 路径使用 |
| `APP__MODEL__TEMPERATURE` | 可选，0 到 2；chat 路径使用 |
| `APP__MODEL__JSON_RESPONSE` | `true`；chat 路径请求 JSON object |
| `APP__MODEL__THINKING` | `default`、`enabled`、`disabled`；非 default 作为 chat extra body |
| `APP__MODEL__PROMPT_CACHE` | `false`；Responses 路径添加 prompt cache key |
| `APP__MODEL__STRUCTURED_OUTPUT_RETRIES` | `1`；结构化 JSON 无效或不完整时重新生成整个对象，0 到 3 |

### 工具 `APP__TOOLS__*`

| 变量 | 默认值/作用 |
|---|---|
| `APP__TOOLS__TIMEOUT_SECONDS` | `15`；共享 `httpx.AsyncClient` timeout |
| `APP__TOOLS__TMDB__ACCESS_TOKEN` | 可选；存在时启用 TMDB metadata |
| `APP__TOOLS__TMDB__API_BASE_URL` | `https://api.themoviedb.org/3` |
| `APP__TOOLS__TMDB__WEB_BASE_URL` | `https://www.themoviedb.org/movie` |
| `APP__TOOLS__TMDB__SEARCH_RESULT_LIMIT` | `8`，1 到 20 |
| `APP__TOOLS__TMDB__OVERVIEW_LENGTH` | `1000`，正整数 |
| `APP__TOOLS__BRAVE__API_KEY` | 可选；存在时 Brave 取代 DDG web provider |
| `APP__TOOLS__BRAVE__ENDPOINT` | Brave LLM Context endpoint |
| `APP__TOOLS__BRAVE__QUERY_LENGTH` | `600` |
| `APP__TOOLS__BRAVE__MAXIMUM_URLS` | `4` |
| `APP__TOOLS__BRAVE__MAXIMUM_TOKENS` | `2048` |
| `APP__TOOLS__BRAVE__MAXIMUM_TOKENS_PER_URL` | `512` |
| `APP__TOOLS__BRAVE__SNIPPETS_PER_URL` | `2` |
| `APP__TOOLS__BRAVE__EXCERPT_LENGTH` | `1400` |
| `APP__TOOLS__DUCKDUCKGO__ENABLED` | `true`；无 Brave key 时启用免费搜索 |
| `APP__TOOLS__DUCKDUCKGO__ENDPOINT` | `https://html.duckduckgo.com/html/` |
| `APP__TOOLS__DUCKDUCKGO__QUERY_LENGTH` | `600` |
| `APP__TOOLS__DUCKDUCKGO__RESULT_LIMIT` | `4`，1 到 20 |
| `APP__TOOLS__CRAWLER__ENABLED` | `true`；false 时只使用 DDG snippet |
| `APP__TOOLS__CRAWLER__MAX_PAGES` | `4`，1 到 20 |
| `APP__TOOLS__CRAWLER__RESPONSE_BYTE_LIMIT` | `1000000` |
| `APP__TOOLS__CRAWLER__EXCERPT_LENGTH` | `1400` |
| `APP__TOOLS__CRAWLER__USER_AGENT` | `CineContext/0.1 (+https://github.com/)` |
| `APP__TOOLS__CRAWLER__MAX_REDIRECTS` | `3`，0 到 10 |
| `APP__TOOLS__OMDB__API_KEY` | 可选；存在时启用 rating lookup |
| `APP__TOOLS__OMDB__ENDPOINT` | `https://www.omdbapi.com/` |
| `APP__TOOLS__OMDB__IMDB_BASE_URL` | `https://www.imdb.com/title` |

也可覆盖 `APP__DATABASE__URL`、`APP__DATABASE__ECHO`、`APP__DATABASE__CREATE_SCHEMA`，但正常部署应使用 `DATABASE_URL` 和 Alembic；仓库 appsettings 将 `create_schema` 设为 false。

## 免费检索与付费 provider

- 无 Brave key：使用 DuckDuckGo 的无 JavaScript HTML endpoint；默认最多 4 个结果并抓取最多 4 个公开 HTML/text 页面。
- crawler 拒绝非 HTTP(S)、带 userinfo、localhost、非公网 DNS 地址和不支持的 content type，逐跳验证重定向并执行字节上限。
- 页面抓取失败但搜索 snippet 存在时，snippet 仍可作为 `search_summary` evidence。
- 有 Brave key 时使用 Brave。否则 TACC 配置使用 Wikipedia，默认配置使用 DuckDuckGo 和 crawler。
- TMDB access token 独立启用电影 metadata、海报路径和 TMDB audience score。
- OMDb key 独立启用 IMDb ID 对应的 IMDb/Rotten Tomatoes/Metacritic 快照。

免费路径可能被限流，不能渲染 JavaScript，并受站点政策与 `robots.txt` 约束。部署者应设置可识别的 user agent 和适当请求限制。

## 健康检查

```bash
curl http://127.0.0.1:8765/api/health
```

响应包含 `status`、`model_mode`、`model_id`、`model_status`、`web_enabled`、`metadata_enabled`、`ratings_enabled`。`model_status` 在 TACC 模式下报告作业是否就绪。工具字段报告配置状态。

## 日志与故障关联

应用只配置 `cinecontext` logger 层级，不改变 Uvicorn、SQLAlchemy 或 Alcedo 的全局 logging。默认同时写 stderr 和根目录 `.runtime/logs/cinecontext.log`，文件达到 10 MB 后轮转并保留 5 份。可用以下变量覆盖：

| 变量 | 默认值/作用 |
|---|---|
| `APP__LOGGING__LEVEL` | `INFO`；可选 `DEBUG`、`INFO`、`WARNING`、`ERROR` |
| `APP__LOGGING__CONSOLE_ENABLED` | `true`；输出到 stderr |
| `APP__LOGGING__FILE_ENABLED` | `true`；启用轮转文件 |
| `APP__LOGGING__PATH` | `.runtime/logs/cinecontext.log`；相对路径从仓库根解析 |
| `APP__LOGGING__MAX_BYTES` | `10000000`；单文件轮转阈值 |
| `APP__LOGGING__BACKUP_COUNT` | `5`；轮转文件数量 |
| `APP__LOGGING__INCLUDE_TECHNICAL_FAILURE_DETAIL` | `true`；在 `run.error` 中附加清理、截断后的异常类型和消息 |
| `APP__LOGGING__CAPTURE_MODEL_RESPONSES` | `false`；仅本地诊断时开启，把原始模型响应写入隔离目录 |
| `APP__LOGGING__MODEL_RESPONSE_PATH` | `.runtime/logs/model-responses`；原始响应诊断目录 |

实时查看：

```powershell
Get-Content .\.runtime\logs\cinecontext.log -Wait -Tail 100
```

```bash
tail -n 100 -f .runtime/logs/cinecontext.log
```

排障时先从 API 返回的 `run_id` 或 `run.error` 中的 `diagnostic_id` 搜索日志。文件日志包含节点、transition、operation、耗时和完整 Python stack；数据库 `tool_calls` 是单次模型/工具 operation 的状态、耗时和安全失败摘要；`run.error` 是面向用户的终态消息与诊断关联 ID。三者用途不同，不应把文件日志直接作为 API 响应。

`INCLUDE_TECHNICAL_FAILURE_DETAIL=true` 有利于当前调试，但即使经过常见 token、key、password、Authorization 和 URL userinfo 清理，第三方异常文本仍可能包含未识别的敏感值。高敏感生产环境可将其设为 `false`，此时 `run.error` 只保留友好消息和 diagnostic ID。日志代码不得记录 prompt、query、evidence/response body、headers、`.env`、数据库凭据或 API key；新增 provider 时仍需审查异常消息与 URL 字段。

结构化输出持续失败时，可在受控本地环境临时设置 `APP__LOGGING__CAPTURE_MODEL_RESPONSES=true`。每次响应会单独写入 `MODEL_RESPONSE_PATH`，文件名包含 run ID、schema 和 attempt，并记录 finish reason/content length。文件正文可能包含用户问题和来源内容，不能上传、提交或在共享生产环境长期启用；诊断完成后应关闭并删除目录。

如果日志出现 `finish_reason=length`、`content_length=0`，同时 token usage显示大量 completion tokens，通常表示兼容 endpoint把生成预算消耗在 `reasoning_content`而没有产出可见 JSON。诊断文件会同时记录 `reasoning_length`；结构化 JSON任务应优先配置 `APP__MODEL__THINKING=disabled`，前提是供应商支持该参数。

## 测试与冒烟验证

单元测试不要求已启动服务：

```bash
./.venv/bin/python -m unittest tests.test_ai_workflow
```

Windows：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_ai_workflow
```

启动 mock 后，在另一终端运行 smoke：

```bash
./.venv/bin/python tests/smoke.py
```

smoke 会创建真实 run、轮询 terminal 状态，并验证 timeline、引用和评分。它会写入所连接的数据库。

## 停止与重启

用 `Ctrl+C` 停止 Uvicorn。脚本自动启动的 PostgreSQL 不会随 Uvicorn 自动停止：

```bash
pg_ctl -D .runtime/postgres -m fast stop
```

PowerShell 同样可执行 `pg_ctl -D .runtime\postgres -m fast stop`，必要时使用 PostgreSQL `bin` 下的完整路径。Docker 数据库使用：

```bash
docker stop cinecontext-postgres
docker start cinecontext-postgres
```

重启应用会再次执行迁移和前端构建，并把数据库中遗留的 queued/running run 标记为 interrupted。再次提交应使用新的 submission key；旧 run 不自动恢复。

## 常见问题

| 现象 | 检查与处理 |
|---|---|
| `initdb/pg_ctl was not found` | 安装 PostgreSQL/加入 PATH，或设置指向已有 PostgreSQL 的 `DATABASE_URL` |
| `database URL is required` | 未经脚本直接启动 app 时需设置 `DATABASE_URL` 或传 `create_app(database_url=...)` |
| asyncpg 密码认证/URL 解析失败 | 检查 role/database 权限，并 percent-encode 用户名和密码组件 |
| Alembic 找不到配置/模块 | 使用 editable install；从 `backend` 运行，或从根目录加 `-c backend/alembic.ini` |
| production 启动时报 model credentials | 设置非空 `APP__MODEL__API_KEY` 和 `APP__MODEL__MODEL_ID`；确认 `.env` 没覆盖 shell 值 |
| production requires `model.mode=openai or tacc` | 检查 `APP__MODEL__MODE` 和 `APP_ENVIRONMENT` |
| health 显示 provider disabled | 检查对应 token/key 名和双下划线；重启后配置才重新装配 |
| DDG 有结果但正文为空 | 站点可能阻止、超限、非 HTML 或 DNS/URL 安全检查失败；系统可能降级为 snippet |
| run 显示 `Diagnostic <id>` | 用 ID 搜索 `.runtime/logs/cinecontext.log` 获取 stack，再用同一 `run_id` 对照节点日志和 `tool_calls` |
| 页面仍是旧前端 | 启动脚本会重建 dist；手工启动 Uvicorn 前先运行 `npm --prefix frontend run build` |
| 端口占用 | 设置 `CINECONTEXT_PORT`；自动 PG 冲突则设置 `CINECONTEXT_DB_PORT` 或 `DATABASE_URL` |
| run 重启后为 interrupted | 当前后台任务不持久化调度，这是预期行为；创建新 submission key 重跑 |
