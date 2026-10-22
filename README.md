# 辅导员 AI 问答机器人（Python）

面向高校学生的辅导员 AI 问答机器人：学生通过微信公众号咨询奖助、教务、宿舍等政策，系统基于知识库和学校官网公开通知给出带出处的回答；遇到心理危机或安全应急时走固定兜底话术，并通知辅导员。

> **当前状态（2026-10-22）**：步骤 1～10 的代码均已落地，`docker compose up -d --build` 可启动，`pytest -q` 40 项通过。已实现：知识库入库、混合检索、规则链与 `/api/ask`、微信回调、管理后台、官网通知采集与定时调度、钉钉危机告警。**仍未验证**：微信真实公众号报文、钉钉真实送达（缺测试号与 webhook）。真实知识库数据源、新仓库、新服务器仍为**待定**。

## 目录

- [功能列表](#功能列表)
- [技术栈](#技术栈)
- [架构简图](#架构简图)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [环境变量](#环境变量)
- [知识库准备](#知识库准备)
- [API 概览](#api-概览)
- [测试](#测试)
- [Docker 部署](#docker-部署)
- [常见问题与排障](#常见问题与排障)
- [安全与隐私](#安全与隐私)
- [相关文档](#相关文档)

## 功能列表

| 功能 | 目标行为 | 当前状态 |
|---|---|---|
| 政策问答 | 混合检索新知识库与官网通知，调用 LLM 生成带出处回答 | 已实现 |
| 危机兜底 | 命中危机关键词直接返回固定话术，不经过 LLM，异步发送告警 | 已实现 |
| 应急场景 | 诈骗、被盗、人身威胁、突发疾病等场景返回固定处置话术 | 已实现 |
| 情绪分级 | `venting` 只共情；`consult` 共情后继续检索政策条款 | 已实现 |
| 官网通知采集 | 抓取学校官网公开通知，SHA-256 去重，定时入库 | 已实现 |
| 管理后台 | HTTP Basic 认证、日志查询、统计看板、手动触发采集 | 已实现 |
| 危机告警 | 钉钉机器人加签、按 openid 去重、回写 `alert_sent` | 已实现（真实送达未验证） |
| 微信回调 | 签名校验、文本异步回复、关注欢迎语、AES 安全模式 | 已实现（真实报文未验证） |
| 健康检查 | `GET /api/health` 返回纯文本 `OK` | 已实现 |

**明确不做**：不登录学校系统、不抓取非公开页面、不存储个人凭据；不做持续对话式心理咨询、诊断或用药建议；不编造条款、通知、联系方式或热线；本期不做多轮上下文、语音识别、转人工、办事流程指引。

## 技术栈

| 层 | 选型 |
|---|---|
| 运行时 | Python 3.11+ |
| Web | FastAPI + Uvicorn |
| 配置 | Pydantic v2 + pydantic-settings |
| ORM | SQLAlchemy 2.0 |
| 数据库 | PostgreSQL 15+ + pgvector（`pgvector/pgvector:pg17`） |
| 迁移 | Alembic |
| LLM | `openai` SDK，`base_url` 指向 DeepSeek |
| Embedding | 任意 OpenAI 兼容服务；默认 SiliconFlow `BAAI/bge-m3`（1024 维，以选型确认为准）。DeepSeek 平台没有 embedding 接口 |
| 微信 | `wechatpy` |
| 采集 | `httpx` + `beautifulsoup4` + `lxml` |
| 定时 | APScheduler |
| 管理后台密码 | `passlib[bcrypt]` |
| 测试 | pytest + pytest-asyncio + httpx |

不要引入 Playwright/Selenium，也不要为了“跑通”换成 SQLite。

## 架构简图

目标架构（当前仅健康检查已实现）：

```text
微信用户
   │  文本 / 关注 / 菜单事件（明文或 AES 安全模式）
   ▼
GET|POST /wechat/callback
   │  文本消息立即返回空串，后台异步处理
   ▼
FastAPI ──► 规则链（QaService）
   │          1. 危机关键词
   │          2. 意图归一化
   │          3. emergency 场景 / urgency=high
   │          4. 负面情绪 venting / consult
   │          5. RAG（新知识库 + 官网通知）
   │          6. 留空板块
   │          7. 低相关度拒答
   ▼
RAG（新知识库 + 官网通知）──► LLM（DeepSeek）──► 微信客服消息 ──► 学生
   │                              │
   │                              └──► qa_log（只读审计）
   └──► 官网通知（定时采集）           └──► 钉钉危机告警（仅 crisis）
```

规则链优先级固定为：**危机关键词 > emergency > urgency=high > 负面情绪(venting/consult) > RAG（通知/知识库）> 留空板块 > 低相关度拒答**。危机判断之前不得调用 LLM。检索排在留空板块之前，否则"库内没有条款但官网发了通知"的问题会被判成空板块。

## 目录结构

当前实际文件：

```text
.
├── .env.example
├── .gitignore
├── AGENTS.md              # AI 编码代理工作守则
├── README.md              # 本文件
├── 开发步骤提示词.md       # 分步执行手册（根目录）
├── requirements.txt
├── requirements-dev.txt
├── pytest.ini
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── alembic.ini
├── alembic/
│   ├── env.py             # 连接串与元数据来自 app/config.py
│   └── versions/          # 迁移脚本
├── app/
│   ├── __init__.py
│   ├── main.py            # FastAPI 实例、lifespan、路由注册
│   ├── config.py          # Settings（pydantic-settings）
│   ├── database.py        # engine、SessionLocal、get_db
│   ├── models/            # knowledge_base / official_notice / qa_log
│   ├── schemas/           # Pydantic 模型（对外 camelCase）
│   ├── repositories/      # SQL 封装
│   ├── services/          # 检索等业务逻辑
│   ├── resources/         # 词表与 Prompt（crisis-keywords / sections / intent-prompt 等 .yml）
│   ├── static/admin/      # 管理后台单页（无外部资源依赖）
│   └── routers/
│       └── qa.py          # GET /api/health
├── scripts/
│   ├── ingest_knowledge.py # 知识库入库（支持 --dry-run）
│   └── fetch_notices.py    # 官网通知采集（支持 --dry-run）
├── data/
│   └── knowledge/
│       └── examples/      # 可提交的样例模板（*.example.md + manifest.example.csv）
├── tests/
│   ├── conftest.py        # 测试库创建、迁移、清表
│   ├── cases/rule_chain.yml
│   └── test_*.py
└── docs/
    ├── 接口契约.md
    ├── 数据库.md
    └── 踩坑记录.md
```

目标结构（后续步骤逐步创建）：

```text
.
├── app/
│   ├── main.py            # FastAPI 实例、lifespan、路由注册、调度器
│   ├── config.py          # Settings（pydantic-settings）
│   ├── database.py        # engine、SessionLocal、get_db
│   ├── models/            # knowledge_base / official_notice / qa_log
│   ├── schemas/           # Pydantic v2 请求/响应
│   ├── repositories/      # SQL 封装
│   ├── services/          # 规则链、RAG、LLM、微信、告警、采集
│   ├── routers/           # qa / wechat / admin / alert
│   ├── resources/         # crisis-keywords.yml、emergency-scenarios.yml 等
│   └── static/admin/      # 管理后台单页
├── scripts/               # 知识库入库、通知采集
├── data/
│   └── knowledge/         # 真实知识库（本地，不入 Git；Markdown + YAML + manifest.csv）
│       └── examples/      # 可提交的样例模板（*.example.md + manifest.example.csv）
├── alembic/               # 数据库迁移
├── tests/                 # pytest
├── docs/
├── .env.example
├── requirements.txt
├── requirements-dev.txt
├── Dockerfile
└── docker-compose.yml
```

## 快速开始

### 0. 前置检查（当前可执行）

```bash
python --version
docker --version
docker compose version
git --version
```

要求 Python 3.11+、Docker Engine 24+、Docker Compose v2。Windows 上若默认 `python` 不是 3.11+，用 `py -3.11 --version` 确认版本，并在后续命令中用 `py -3.11` 代替 `python`。

### 1. 安装依赖（已实现）

```bash
py -3.11 -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt -r requirements-dev.txt
```

Linux/macOS 用 `python3.11 -m venv .venv`，激活命令为 `source .venv/bin/activate`。

### 2. 准备环境变量（已实现）

```bash
cp .env.example .env
```

编辑 `.env`，至少填写 `DB_PASSWORD`、`DEEPSEEK_API_KEY`、`EMBEDDING_API_KEY`、`WECHAT_TOKEN`、`WECHAT_APP_ID`、`WECHAT_APP_SECRET`、`COUNSELOR_CONTACT`、`ADMIN_PASSWORD`。步骤 1 只启动健康检查，不填这些值也能启动；后续功能需要它们。不要把 `.env` 提交到 Git。

### 3. 启动 PostgreSQL + pgvector（已实现）

```bash
docker compose up -d postgres
docker compose ps
```

镜像为 `pgvector/pgvector:pg17`，只监听 `127.0.0.1:5432`，不对公网开放；`POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` 取自 `.env` 的 `DB_USER`/`DB_PASSWORD`/`DB_NAME`，缺 `DB_PASSWORD` 时 compose 会直接报错。

不用 Docker 时也可以手工起一个容器，但 compose 是推荐方式（数据卷、健康检查都配好了）。

### 4. 执行数据库迁移（已实现）

```bash
alembic upgrade head
alembic downgrade -1
```

`CREATE EXTENSION IF NOT EXISTS vector;` 已包含在初始迁移里，不需要手工执行；迁移会建 `knowledge_base`、`official_notice`、`qa_log` 三张表和 HNSW 索引。结构与字段见 [docs/数据库.md](docs/数据库.md)。

### 5. 启动服务（已实现）

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8081
```

步骤 1 启动时不连接数据库或 LLM；数据库连接配置已就绪，但尚未使用。

### 6. 健康检查（已实现）

```bash
curl http://localhost:8081/api/health
```

预期返回纯文本 `OK`。端口统一为 `8081`，不使用 `8080`。

## 环境变量

完整清单以 `.env.example` 为准；以下为关键项：

| 变量 | 必填 | 默认/示例 | 说明 |
|---|---|---|---|
| `DB_HOST` | 是 | `localhost` | 数据库地址 |
| `DB_PORT` | 是 | `5432` | 数据库端口 |
| `DB_NAME` | 是 | `counselor_bot` | 数据库名，使用本项目独立库名 |
| `DB_USER` | 是 | `counselor` | 数据库用户，生产应使用独立账号 |
| `DB_PASSWORD` | 是 | 无 | 生产必须使用独立强密码 |
| `DEEPSEEK_BASE_URL` | 是 | `https://api.deepseek.com/v1` | 必须带 `/v1` |
| `DEEPSEEK_API_KEY` | 是 | 无 | LLM 密钥 |
| `DEEPSEEK_MODEL` | 否 | `deepseek-flash` | 主生成模型；可换 `deepseek-v4-pro` |
| `DEEPSEEK_TIMEOUT` | 否 | `60` | 秒；兼容旧 `PT60S` 写法 |
| `EMBEDDING_BASE_URL` | 是 | `https://api.siliconflow.cn/v1` | Embedding 服务，任何 OpenAI 兼容地址 |
| `EMBEDDING_API_KEY` | 是 | 无 | Embedding 密钥 |
| `EMBEDDING_MODEL` | 否 | `BAAI/bge-m3` | 默认输出 1024 维，以选型确认为准 |
| `EMBEDDING_TIMEOUT` | 否 | `30` | 秒；兼容旧 `PT30S` 写法 |
| `EMBEDDING_DIMENSIONS` | 否 | 无 | 仅 MRL 模型可用（如本地 `qwen3-embedding:4b` 原生 2560 维，填 `1024` 截断）；留空按模型原生维度。必须与 `VECTOR_DIMENSION` 一致 |
| `VECTOR_DIMENSION` | 否 | `1024` | 与 embedding 模型一致 |
| `WECHAT_TOKEN` | 是 | 无 | 微信签名；为空时回调拒绝，不放行 |
| `WECHAT_APP_ID` | 是 | 无 | 公众号 AppID；兼容旧 `WECHAT_APPID`。**安全模式的 AES 解密会校验报文内嵌 appid 必须等于它，不能留空** |
| `WECHAT_APP_SECRET` | 是 | 无 | 公众号 AppSecret；兼容旧 `WECHAT_APPSECRET`；留空时客服消息发送会跳过 |
| `WECHAT_ENCODING_AES_KEY` | 否 | 无 | 安全模式必填；43 位 base64（不要带 `=`） |
| `COUNSELOR_CONTACT` | 是 | 无 | 辅导员姓名+电话，用于转介话术 |
| `COUNSELOR_OFFICE_HOURS` | 否 | `工作日 8:30-17:30` | 办公时间 |
| `RETRIEVAL_MIN_SCORE` | 否 | `0.55` | 知识库检索阈值；新知识库需重新校准 |
| `RETRIEVAL_TOP_K` | 否 | `5` | 知识库检索条数 |
| `NOTICE_RETRIEVAL_MIN_SCORE` | 否 | `0.55` | 通知检索阈值 |
| `NOTICE_RETRIEVAL_TOP_K` | 否 | `5` | 通知检索条数 |
| `NOTICE_FETCH_BASE_URL` | 是（采集） | `https://www.nynu.edu.cn/` | 官网通知栏目所在站点；留空则不启动定时采集 |
| `NOTICE_FETCH_PAGES` | 否 | `3` | 每次抓取页数 |
| `NOTICE_FETCH_TIMEOUT_MS` | 否 | `15000` | 采集超时 |
| `NOTICE_FETCH_CRON` | 否 | `0 */6 * * *` | APScheduler 5 段，按 **UTC** 解释；6 段 cron 需转换 |
| `DINGTALK_WEBHOOK` | 否 | 无 | 钉钉机器人 webhook；为空则关闭告警（危机仍回复，`alert_sent=false`） |
| `DINGTALK_SECRET` | 否 | 无 | 钉钉加签密钥（`SEC...`）；与 webhook 配套，缺一个都发不出去 |
| `ALERT_DEDUP_MINUTES` | 否 | `10` | 同一 openid 去重窗口；`0` 表示不去重 |
| `ADMIN_USERNAME` | 否 | `admin` | 管理后台用户名 |
| `ADMIN_PASSWORD` | 是（生产） | 无 | 生产必须显式设置；不要留空 |
| `SERVER_PORT` | 否 | `8081` | 服务端口 |

另有 `INTENT_DEEPSEEK_*`、`COMFORT_DEEPSEEK_*` 等可选模型覆盖项，完整清单以 `.env.example` 为准。企业微信告警未实现，不要配置不存在的 `ALERT_CHANNEL`。

## 知识库准备

> **新知识库数据源待定**：历史数据文件（JSON、PDF、映射表等）不作为新数据源，也不直接导入；新数据源确定后，按下面的格式准备。

### 输入格式

每个来源文档一个 `.md` 文件，文件顶部是文档级 YAML front matter；`manifest.csv` 是文件清单。

```text
data/knowledge/
├── manifest.csv
├── 01-学籍管理规定.md
└── 02-学生公寓管理办法.md
```

`manifest.csv` 示例（前五列固定，后三列可选，行内容按实际文档替换）：

```csv
file,doc_title,doc_num,category,source_url,version,effective_date,status
01-学籍管理规定.md,XX学院学籍管理规定,1,学业教务,https://example.edu.cn/notice/1,2025版,2025-09-01,现行
02-学生公寓管理办法.md,XX学院学生公寓管理办法,2,宿舍管理,https://example.edu.cn/notice/2,2024版,2024-09-01,现行
```

`version`、`effective_date`、`status` 为可选列，但建议提供，便于判断条款是否过期；缺省时按空值处理，不影响入库。

Markdown 示例：

```markdown
---
doc_title: XX学院学籍管理规定
doc_num: 1
category: 学业教务
source_url: https://example.edu.cn/notice/1
---

# 第一章 总则

## 第一条 适用范围

本条正文，保留原文，不改写、不总结、不补全。

## 第二条 学制

本条正文。

### （一）条内小节

小节内容归入第二条，不单独入库。
```

### 切分规则

标题约定统一如下（本文件、`docs/踩坑记录.md`、`开发步骤提示词.md` 三处必须一致）：

条的位置按标题文本识别，**不限定 Markdown 级别**，`##` 或 `###` 都可以；每个文件只能有一个章标题。

| 层级 | 标题示例 | 正则 | 是否切分 |
|---|---|---|---|
| 章 | `# 第三章 奖助评优` | `^#{1,6}\s+第.+章` | 作为 `chapter` 元数据 |
| 节（可选） | `## 第三节 奖学金评定` | `^#{1,6}\s+第.+节` | 作为 `section` 元数据 |
| 条 | `### 第二十五条` | `^#{1,6}\s+第.+条` | 每个条一条记录 |
| 其他标题 | `### （一）……`、`## 常见问答` | — | 条内归入正文；条外记 warning |

- 条号可单独成标题，也可与标题同行（`第二十五条` / `第二十五条 补考与重修`）；`article_num` 取条号，`article_title` 取条号后的文本，可为空。
- 一个文件只能有一个章标题；缺章标题时取 front matter 的 `chapter`，仍缺失则报错停止。
- 文档级 front matter 至少包含 `doc_title`、`category`；允许改由 `manifest.csv` 提供，合并后仍缺则报错。可选 `doc_num`、`source_url`、`chapter`。
- 按“条”切分：一个条标题到下一条/下一章之间为一个记录，一条记录只含一条条款；条内其他标题留在所属条正文里，不单独成条。
- 正文保留原文；禁止改写、总结、补全或把整章塞成一条。
- 没有条标题的文件，只有 front matter 显式提供 `article_num` 时才允许作为单条入库；否则报错停止。
- `id` 由 `doc_num`（缺省用文件 slug）+ `chapter` + `article_num` 稳定生成；重复入库按 `id` upsert，不产生重复记录。
- `manifest.csv` 必须覆盖目录下每个 `.md` 且不重复；缺失、多余、空正文、缺 `article_num` 都直接失败，不静默跳过。条外无法识别的标题打印 warning，并在 `--dry-run` 汇总，不中断。
- embedding 文本 = `doc_title + chapter + article_title + content`；维度默认 `1024`（以选型确认为准）；建 HNSW cosine 索引；换模型必须重建向量列与索引。

### 入库命令（已实现）

```bash
# 先校验数据：不写库、不计算 embedding
python scripts/ingest_knowledge.py --input data/knowledge --dry-run
# 正式入库：需要 .env 里的 EMBEDDING_API_KEY
python scripts/ingest_knowledge.py --input data/knowledge
```

- `--dry-run` 只打印文件数、条数、warning 和重复 `id`，不写库、不调用 embedding；首次入库前先跑。
- `--manifest` 指定 manifest 路径，默认 `<input>/manifest.csv`。
- 按 `id` upsert（`ON CONFLICT (id) DO UPDATE`），重复执行不产生重复行。
- 空正文、缺 `article_num`、front matter 解析失败、manifest 与实际文件不一致，都直接报错退出，不静默跳过。
- 可提交的样例在 `data/knowledge/examples/`。本地演练时把它们复制到 `data/knowledge/`，并把 `manifest.example.csv` 改名为 `manifest.csv`；该目录已被 `.gitignore` 排除，不会提交。

## 检索（已实现）

`app/services/rag_service.py` 做混合检索，不接 LLM：

- **向量路**：按 `topK * 2` 取余弦最近邻（pgvector HNSW 索引）。
- **关键词路**：问题按 `[，。！？?,!\s]+` 切词，长度 ≥ 2、去停用词（`app/resources/stopwords.yml`）、去重、最多 8 个；每个关键词对 `content` 做 `ILIKE '%kw%'`，每个词最多 5 条。
- **融合**：只命中关键词的条目记 `0.35`；同时命中向量与关键词的记 `min(0.95, 向量分 + 0.08)`；只命中向量的保留原分。按融合分降序取 `topK`。

`RETRIEVAL_MIN_SCORE`（拒答阈值）与 embedding 模型强相关：不同模型的相似度分布不可比，**换模型必须用真实问题重新标定**，不要沿用旧值。

已知限制：按标点切词时，自然问句（如「宿舍能用电磁炉吗」）常只切出一个长 token，`ILIKE` 基本命中不到；关键词路主要在问题里出现条款原词（如「大功率电器」）时才起作用。切分集合是 `[，。！？?,!\s]+`，**顿号 `、` 不在其中**，用顿号分隔的词会被当成一个整体。

## 通知采集（已实现）

只抓学校官网公开的「通知公告」栏目（`NOTICE_FETCH_BASE_URL`），不登录、不绕鉴权、不使用浏览器自动化。站点未提供 `robots.txt`（404，无限制声明）。

```bash
python scripts/fetch_notices.py --once --dry-run --pages 1   # 只解析：不写库、不算向量
python scripts/fetch_notices.py --once --pages 1             # 抓取并入库
```

南阳师范学院官网（2026-10 实测）的页面结构：

- 列表首页 `index/tzgg.htm`；第 N 页是 `index/tzgg/{总页数+1-N}.htm`——**页号倒序**，总页数从分页块的 `span.p_no` 里取最大值，不要自己按 `?page=N` 拼。
- 列表项 `div.i-list ul > li > a > div.date > b(日)+span(YYYY-MM)`、`div.info > h3(标题)+p(摘要)`；详情链接是**相对路径**（首页 `../info/...`、翻页后 `../../info/...`），必须按当前页 URL 解析。
- 详情页标题取 `<title>`（去掉 `-南阳师范学院` 后缀），发布时间在页面文本 `日期: YYYY-MM-DD HH:MM`（北京时间，入库前转 UTC），正文在 `div.v_news_content`，取文本前要剔除内嵌的 `<style>`。

其它规则：

- 去重：先按 `url` 跳过已入库条目的详情请求；写入时按 `content_hash`（正文去空白后 SHA-256）唯一约束 `ON CONFLICT DO NOTHING`，同一内容换个链接重发也不会重复。
- embedding 文本 = `标题 + 正文`；维度与 `vector(1024)` 一致。
- 定时采集由 APScheduler 驱动（`NOTICE_FETCH_CRON`，UTC），未配置 `NOTICE_FETCH_BASE_URL` 时不启动。**多 worker 部署会每个进程各跑一份**，见 `docs/踩坑记录.md`。
- 详情请求之间限速 0.3 秒；抓不到正文的通知记 warning 并跳过，不写空记录。

## 危机告警（已实现）

`crisis` 分支触发钉钉机器人（加签模式），文案在 `app/resources/alert-template.yml`。

- **加签**：`base64(HMAC-SHA256(secret, "timestamp\nsecret"))` 再做 URL 编码，拼到 webhook 的 `timestamp` / `sign` 参数上。日志与异常信息里不打印 webhook 和 secret。
- **成功判定**：钉钉即使限流或签名错也返回 HTTP 200，必须看响应体的 `errcode == 0`；`errcode != 0` 记为失败。
- **去重**：同一 `openid` 在 `ALERT_DEDUP_MINUTES` 窗口内只告警一次。判断依据是 `qa_log` 里该 openid 近期是否有 `is_high_risk=true 且 alert_sent=true` 的记录——用数据库而不是进程内字典，多 worker 下也成立。`/api/ask` 不带 openid 时不做去重。
- **`alert_sent` 语义**：发送成功 `true`；被去重抑制或发送失败 `false`。
- **先发告警、再写日志**：`qa_log` 按 AGENTS 只允许 INSERT，所以 `alert_sent` 必须在写日志前确定，不能事后 UPDATE。代价是危机回复会等一次告警请求（超时 5 秒），失败不影响学生收到回复。
- **未配置 webhook 时**：危机分支照常回复，只记一条 warning，`alert_sent=false`。

告警测试接口是 `POST /admin/api/alert/test`，**必须带 Basic 认证**——它会真的往授权群发消息，不能对公网裸奔。它不参与去重、不写 `qa_log`。

## API 概览

`/api/health`、`/api/ask`、`/wechat/callback`、`/admin/api/logs`、`/admin/api/stats`、`/admin/api/fetch-notices`、`/admin/` 已实现（`/api/ask` 走完整规则链并写 `qa_log`）；其余接口仍为目标契约。

> **`/wechat/callback` 尚未用真实公众号报文验证**：本地已用自造报文验过签名校验、明文文本、关注、菜单点击、AES 加解密往返与分段，但 `docs/踩坑记录.md` 明确写过"微信 AES/签名必须用真实回调报文实测"。拿到测试号后必须补验一次。除 `/admin/**` 外不鉴权；`/admin/**` 使用 HTTP Basic。

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/ask` | 学生提问；请求 `{"question":"..."}`，返回答案、`path`、`sources`、`topSimilarity` |
| `GET` | `/api/health` | 纯文本 `OK` |
| `GET` | `/wechat/callback` | 微信签名校验；成功返回 `echostr`，失败返回 `fail` |
| `POST` | `/wechat/callback` | 文本消息异步处理；关注/菜单事件同步返回 XML；安全模式解密/加密 |
| `POST` | `/admin/api/fetch-notices` | 手动触发官网通知采集；参数 `pages`（0 表示用配置值），返回抓取条数与新增条数 |
| `GET` | `/admin/api/logs` | 日志分页查询；参数 `startDate`、`endDate`、`path`、`emotion`、`keyword`、`page`、`size` |
| `GET` | `/admin/api/stats` | 统计：`daily`、`pathDistribution`、`rejectionRate`、`recentCrisis`、`recentVenting` |
| `GET` | `/admin/` | 管理后台单页（只读：日志 + 统计） |
| `POST` | `/admin/api/alert/test` | 告警测试（需 Basic 认证）；JSON 参数 `msg`、`openid`；返回 `{"success":true,"message":"..."}`；`success` 严格等于钉钉 `errcode == 0` |

`POST /api/ask` 响应示例（字段必须 camelCase）：

```json
{
  "question": "我挂科了怎么办？",
  "answer": "根据《XX学院学籍管理规定》第二十五条……",
  "path": "normal",
  "sources": [
    {
      "docTitle": "XX学院学籍管理规定",
      "chapter": "第三章 成绩管理",
      "articleNum": "二十五",
      "articleTitle": "补考与重修",
      "content": "……",
      "similarity": 0.78
    }
  ],
  "topSimilarity": 0.78
}
```

`path` 枚举、空问题响应、字段与分页契约见 [docs/接口契约.md](docs/接口契约.md)；`qa_log` 只读，不提供修改/删除接口。

## 测试

```bash
pytest -q
ruff check . && ruff format --check .
```

- 集成测试**必须**连 PostgreSQL + pgvector：`tests/conftest.py` 会自动创建 `counselor_bot_test`（可用 `TEST_DB_NAME` 覆盖）并对它跑 `alembic upgrade head`，每个用例前清空三张表。**数据库不可用时明确 skip，不静默通过**。
- 单测 mock 外部 LLM / embedding / 微信 / 钉钉；不调用真实付费 API，也不打真实 webhook。
- 规则链用例在 `tests/cases/rule_chain.yml`：新增分支必须在那里写期望 `path`，`tests/test_rule_chain.py` 会逐条驱动执行。
- 当前覆盖：知识库切分（两种标题层级、稳定 id、manifest 与空正文报错）、关键词切分与融合分、规则链全分支（含"通知优先于空板块"的回归用例）、camelCase 契约、`qa_log` 只读、微信签名与 AES 往返、告警加签与去重。
- 测试不会写入 `data/knowledge/`，也不提交真实日志。

### 测试数据规则

- 真实知识库放 `data/knowledge/`；`.gitignore` 必须用 `data/knowledge/*` 加反向排除保留 examples 模板，不能写 `data/knowledge/`（整目录忽略后反向排除不会生效）：

```gitignore
# 真实知识库：忽略内容，但保留 examples 模板
data/knowledge/*
!data/knowledge/examples/
!data/knowledge/examples/*.example.md
!data/knowledge/examples/manifest.example.csv
# 测试数据与本地文件
tests/fixtures/real/
.env
.venv/
__pycache__/
*.log
knowledge_base.json
e2e_test_report*
*_test_results.json
```

- 合成测试小样本放 `tests/fixtures/mini/`；微信真实报文脱敏后放 `tests/fixtures/wechat/`，真实 openid 不提交。
- 学生原话、openid、日志、数据库导出、`*.log`、`.env` 一律不提交 Git；测试数据、样例知识库、真实日志只放本地。
- 每个开发步骤只做一次相关验证（跑一次测试或一次 dry-run），不要每次编辑后重复入库、调用真实 API 或微信回调。

## Docker 部署

```bash
docker compose up -d --build
docker compose logs -f app
curl http://localhost:8081/api/health   # 期望 OK
docker compose down                      # 加 -v 会连数据卷一起删
```

编排包含 `postgres`（`pgvector/pgvector:pg17`，只监听 `127.0.0.1:5432`，带健康检查）和 `app` 两个服务；`app` 等 postgres healthy 后才启动，容器内把 `DB_HOST` 覆盖成 `postgres`，端口 `8081:8081`，环境变量从 `.env` 注入。

- `app` 启动命令是 `alembic upgrade head && uvicorn ...`：**迁移失败容器直接退出**，不会带着旧表结构对外服务。
- 镜像不含 `.env`、`.venv`、测试与 `docs`（见 `.dockerignore`）；`data/knowledge/` 的真实文件也不进镜像。
- 容器内访问宿主机的 Ollama 要把 `EMBEDDING_BASE_URL` 改成 `http://host.docker.internal:11434/v1`（compose 已加 `extra_hosts`）；`127.0.0.1` 在容器里指的是容器自己。
- 生产环境不要把默认密码写进 compose；`.env` 权限保持 600，后台必须走 HTTPS。

## 常见问题与排障

**`ModuleNotFoundError: No module named 'app'`**
多半是没在仓库根目录执行。`uvicorn`、`alembic`、两个脚本都要求以仓库根为工作目录；容器里 `/app` 就是根目录。

**数据库连接失败 / `Connection refused`**
确认 `docker compose ps` 中 `postgres` 为 healthy；检查 `DB_HOST`、`DB_PORT`、`DB_NAME`、`DB_USER`、`DB_PASSWORD`。PostgreSQL 只应监听 `127.0.0.1:5432`。

**`extension "vector" is not available`**
使用了普通 PostgreSQL 镜像。改用 `pgvector/pgvector:pg17`，并执行 `CREATE EXTENSION IF NOT EXISTS vector;`。

**`expected 1024 dimensions` / 维度不匹配**
`VECTOR_DIMENSION`、embedding 模型和表结构必须一致。换模型后重建向量列和 HNSW 索引，不要混用旧向量。

**微信回调返回 `fail`**
检查 `WECHAT_TOKEN` 是否配置、签名参数是否完整；`WECHAT_TOKEN` 为空时本项目拒绝回调，不放行。安全模式还需 `WECHAT_ENCODING_AES_KEY` 和 `msg_signature`；回调 URL 必须是公网可达的 HTTPS。

**通知采集结果为 0**
先确认列表页选择器仍匹配。若页面由 JS 动态渲染导致抓不到，停止并报告，**禁止引入 Playwright/Selenium**；不要用浏览器自动化绕过。

**LLM 超时或 401**
检查 `DEEPSEEK_BASE_URL` 是否带 `/v1`、密钥是否有效、超时是否足够。外部调用失败应降级为固定话术，不把堆栈透给学生。

**管理后台 401**
检查 `ADMIN_USERNAME` / `ADMIN_PASSWORD`；生产必须显式设置强密码。`/admin/**` 使用 Basic 认证，建议只允许校园网/VPN 访问。

**`qa_log` 为什么不能删记录**
`qa_log` 是审计与危机追溯依据，按设计只读；没有 UPDATE/DELETE 接口。保留期限与清理策略**待定**，上线前必须与学校确认。

## 安全与隐私

- 只采集学校官网公开页面；禁止登录学校系统、绕过鉴权、抓取非公开内容。
- 禁止在代码、测试、文档、compose 中写真实密钥、密码、webhook、token、手机号；统一走环境变量。
- `.env` 必须加入 `.gitignore`，权限设为 `600`；生产使用全新强密码，不复用历史凭据。
- 学生 openid、原话、告警内容属于个人信息：仅授权人员可查；管理后台必须 HTTPS，建议限校园网/VPN；导出或截图需审批。
- 危机告警只发授权群；应用日志和后台列表对 openid 掩码或哈希，webhook/token/密钥永不打印。
- `qa_log` 只读；不提供修改、删除接口。保留期限、备份与访问审计**待定**，上线前必须明确。
- `/admin/**` 用 HTTP Basic，认证失败 5 次 / 10 分钟锁定；`ADMIN_PASSWORD` 可填明文（本地）或 bcrypt 哈希（以 `$2` 开头，推荐）。**锁定计数在进程内存里，多 worker 下会失效**，上线前要换成共享存储。日志列表对 `openid` 掩码，不提供任何写/删接口。
- 外部调用必须设超时；LLM、微信、钉钉失败只降级为稳定话术，不向学生暴露异常堆栈或供应商响应。
- 限流/防刷、微信回调幂等、CI/CD、数据库定时备份尚未实现，上线前必须补齐。

## 相关文档

- [docs/交接文档.md](docs/交接文档.md)：接手必读——当前状态、怎么跑、还差什么、交接清单。
- [AGENTS.md](AGENTS.md)：AI 编码代理工作守则，含范围红线、分层职责、规则链与测试底线。
- [docs/接口契约.md](docs/接口契约.md)：规则链顺序、`path` 枚举、响应字段、后台分页与 `qa_log` 写入契约。
- [docs/数据库.md](docs/数据库.md)：表结构、索引与迁移规则。
- [docs/踩坑记录.md](docs/踩坑记录.md)：初始化阶段的钩子冲突、环境问题、开发决策与已知规格差异。
- [开发步骤提示词.md](开发步骤提示词.md)：分步实施顺序与每步验收标准（根目录执行版）。
- 新知识库数据源：**待定**。
- 新仓库：**待定**（当前目录尚未初始化 Git 仓库）。
- 新服务器：**待定**。
