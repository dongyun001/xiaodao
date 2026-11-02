# 辅导员 AI 问答机器人

面向高校学生的辅导员问答服务。学生通过微信公众号提问奖助、教务、宿舍等校内政策问题，系统基于知识库和学校官网公开通知给出带出处的回答；命中危机关键词或应急场景时，返回固定处置话术，并通知辅导员。

## 项目状态

截至 2026-10-24：

- 知识库入库、混合检索、规则链、`POST /api/ask`、微信回调、管理后台、官网通知采集与定时调度、钉钉危机告警均已实现。
- 本机 `pytest -q` 40 项通过；`docker compose` 启动后，`GET /api/health` 返回纯文本 `OK`。
- **未联调**：微信真实公众号报文、钉钉真实送达。两者都需要测试号或 webhook。
- **待定**：正式知识库数据源、部署服务器。

## 目录

- [项目状态](#项目状态)
- [功能列表](#功能列表)
- [快速开始](#快速开始)
- [环境变量](#环境变量)
- [API 概览](#api-概览)
- [知识库准备](#知识库准备)
- [检索](#检索)
- [通知采集](#通知采集)
- [危机告警](#危机告警)
- [技术栈](#技术栈)
- [架构简图](#架构简图)
- [目录结构](#目录结构)
- [测试](#测试)
- [Docker 部署](#docker-部署)
- [常见问题与排障](#常见问题与排障)
- [安全与隐私](#安全与隐私)
- [已知限制与路线图](#已知限制与路线图)
- [贡献](#贡献)
- [许可证](#许可证)
- [相关文档](#相关文档)

## 功能列表

| 功能 | 行为 | 当前状态 |
|---|---|---|
| 政策问答 | 混合检索知识库与官网通知，调用 LLM 生成带出处回答 | 已实现 |
| 危机兜底 | 命中危机关键词直接返回固定话术，不经过 LLM，并发送告警 | 已实现 |
| 应急场景 | 诈骗、被盗、人身威胁、突发疾病等场景返回固定处置话术 | 已实现 |
| 情绪分级 | `venting` 只共情；`consult` 共情后继续检索政策条款 | 已实现 |
| 官网通知采集 | 抓取学校官网公开通知，SHA-256 去重，定时入库 | 已实现 |
| 管理后台 | HTTP Basic 认证、日志查询、统计看板；采集与告警测试通过受保护的接口触发 | 已实现 |
| 危机告警 | 钉钉机器人加签、按 openid 去重、记录 `alert_sent` | 已实现（真实送达未验证） |
| 微信回调 | 签名校验、文本异步回复、关注欢迎语、AES 安全模式 | 已实现（真实报文未验证） |
| 健康检查 | `GET /api/health` 返回纯文本 `OK` | 已实现 |

**明确不做**：

- 不登录学校系统、不抓取非公开页面、不存储个人凭据。
- 不做持续对话式心理咨询、诊断或用药建议。
- 不编造条款、通知、联系方式或热线。
- 本期不做多轮上下文、语音识别、转人工、办事流程指引。

## 快速开始

以下命令都在仓库根目录执行；如果还没有代码，先克隆仓库。

### 环境要求

- Python 3.11+
- Docker Engine 24+、Docker Compose v2
- 一个 OpenAI 兼容的 LLM 服务和一个 Embedding 服务。DeepSeek 平台没有 embedding 接口。

只跑 Docker 时，本机主要需要 Docker；本地开发还需要 Python 3.11+。

### 1. 准备配置

```bash
cp .env.example .env
```

编辑 `.env`。至少设置 `DB_PASSWORD`；使用政策问答还需要 `DEEPSEEK_API_KEY`、`EMBEDDING_API_KEY`；使用微信回调需要 `WECHAT_TOKEN`、`WECHAT_APP_ID`、`WECHAT_APP_SECRET`；转介话术需要 `COUNSELOR_CONTACT`；生产环境的管理后台必须设置 `ADMIN_PASSWORD`。只验证健康检查时，除 `DB_PASSWORD` 外的密钥可以先留空。

不要把 `.env` 提交到 Git。完整变量清单见[环境变量](#环境变量)，模板见 `.env.example`。

### 2. 启动

#### 方式 A：Docker Compose（推荐）

```bash
docker compose up -d --build
docker compose ps
curl http://localhost:8081/api/health
```

容器启动时自动执行 `alembic upgrade head`；迁移失败容器直接退出，不会带着旧表结构对外服务。

#### 方式 B：本地开发

```bash
python -m venv .venv
source .venv/bin/activate
# Windows Git Bash: source .venv/Scripts/activate
pip install -r requirements.txt -r requirements-dev.txt

docker compose up -d postgres
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8081
```

Windows 上如果 `python` 不是 3.11+，用 `py -3.11` 创建虚拟环境，并在后续命令中替换 `python`。

初始迁移已包含 `CREATE EXTENSION IF NOT EXISTS vector;`，不需要手工执行；迁移会建 `knowledge_base`、`official_notice`、`qa_log` 三张表和 HNSW 索引。端口统一为 `8081`，不使用 `8080`。

### 3. 初始化数据（可选）

```bash
# 知识库：先校验，不写库、不计算 embedding
python scripts/ingest_knowledge.py --input data/knowledge --dry-run
python scripts/ingest_knowledge.py --input data/knowledge

# 官网通知：先解析，不写库
python scripts/fetch_notices.py --once --dry-run --pages 1
python scripts/fetch_notices.py --once --pages 1
```

正式入库需要 `.env` 中的 Embedding 配置。知识库格式见[知识库准备](#知识库准备)，采集规则见[通知采集](#通知采集)。

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
| `EMBEDDING_MODEL` | 否 | `BAAI/bge-m3` | 默认输出 1024 维，需与 `VECTOR_DIMENSION` 一致 |
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

## API 概览

以下接口均已实现。`POST /api/ask` 走完整规则链，并写一条 `qa_log`。`/admin/**` 使用 HTTP Basic 认证；`/wechat/callback` 依赖微信签名校验；其余接口没有应用层鉴权，上线时应通过反向代理、网关或网络范围限制访问。

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/ask` | 学生提问；请求 `{"question":"..."}`，返回答案、`path`、`sources`、`topSimilarity` |
| `GET` | `/api/health` | 纯文本 `OK` |
| `GET` | `/wechat/callback` | 微信签名校验；成功返回 `echostr`，失败返回 `fail` |
| `POST` | `/wechat/callback` | 文本消息异步处理；关注/菜单事件按需同步返回 XML；安全模式解密/加密 |
| `POST` | `/admin/api/fetch-notices` | 手动触发官网通知采集；参数 `pages`（0 表示用配置值），返回抓取条数与新增条数 |
| `GET` | `/admin/api/logs` | 日志分页查询；参数 `startDate`、`endDate`、`path`、`emotion`、`keyword`、`page`、`size` |
| `GET` | `/admin/api/stats` | 统计：`daily`、`pathDistribution`、`rejectionRate`、`recentCrisis`、`recentVenting` |
| `GET` | `/admin/` | 管理后台单页（只读：日志 + 统计） |
| `POST` | `/admin/api/alert/test` | 告警测试（需 Basic 认证）；JSON 参数 `msg`、`openid`；返回 `{"success":true,"message":"..."}`；`success` 严格等于钉钉 `errcode == 0` |

请求示例：

```bash
curl -X POST http://localhost:8081/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"我挂科了怎么办？"}'
```

响应示例（字段必须 camelCase）：

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

### 微信回调

回调地址填 `https://<域名>/wechat/callback`，必须是公网可达的 HTTPS。GET 用于签名校验，POST 接收文本、关注和菜单事件；文本消息立即返回空串，后台异步发送客服消息，关注事件和能匹配话术的菜单事件同步返回 XML。安全模式需要配置 `WECHAT_ENCODING_AES_KEY`。

**尚未用真实公众号报文验证**。本地已用自造报文验过签名校验、明文文本、关注、菜单点击、AES 加解密往返与分段；拿到测试号后需要补验一次。

### 管理后台

访问 `/admin/`，使用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 走 HTTP Basic。页面只读，展示日志和统计；日志列表对 openid 掩码，没有修改或删除入口。

`POST /admin/api/fetch-notices` 用于手动触发采集；`POST /admin/api/alert/test` 会真的往授权群发消息，必须保护。生产环境应走 HTTPS，建议只允许校园网或 VPN 访问。

## 知识库准备

> 标题与文件组织以 [docs/学生手册-Markdown编写规范.md](docs/学生手册-Markdown编写规范.md) 为准，该文件第 7 节列出入库脚本的附加要求。数据源尚未最终确定；历史数据文件（JSON、PDF、映射表等）不作为新数据源，也不直接导入。

### 输入格式

**一章一个 `.md` 文件**：学生手册按 9 个板块拆成 9 个文件，独立规定可以一个文件一章。文件顶部是文档级 YAML front matter；`manifest.csv` 是文件清单，必须覆盖目录下每个 `.md`。

```text
data/knowledge/
├── manifest.csv
├── 01-总则.md
├── 02-学业教务.md
└── …（共 9 个板块）
```

`manifest.csv` 示例（前五列固定，后三列可选，行内容按实际文档替换）：

```csv
file,doc_title,doc_num,category,source_url,version,effective_date,status
01-总则.md,XX学院学生手册,1,总则,https://example.edu.cn/notice/1,2025版,2025-09-01,现行
02-学业教务.md,XX学院学生手册,1,学业教务,https://example.edu.cn/notice/2,2025版,2025-09-01,现行
```

`version`、`effective_date`、`status` 为可选列，但建议提供，便于判断条款是否过期；缺省时按空值处理，不影响入库。

同一本学生手册的 9 个章文件使用相同的 `doc_num` 与 `doc_title`；独立规定各自单独编号。`id` 由 `doc_num + chapter + article_num` 生成，同一手册内不会冲突。

Markdown 示例：

```markdown
---
doc_title: XX学院学生手册
doc_num: 5
category: 宿舍管理
source_url: https://example.edu.cn/notice/5
---

# 第五章　宿舍管理

## 第一节　入住与退宿

### 第一条

本条正文，保留原文，不改写、不总结、不补全。

### 第二条

本条正文。

（一）条内小节：小节内容归入第二条，不单独入库。
```

### 切分规则

编写时必须按 `#`=章、`##`=节、`###`=条的层级，条号独立成标题行（见 [编写规范](docs/学生手册-Markdown编写规范.md)）。解析器按**标题文本**识别层级、不校验 Markdown 级别，所以历史文件里的 `## 第X条` 仍能入库，但新数据一律按约定编写。

| 层级 | 标题示例 | 识别规则 | 是否切分 |
|---|---|---|---|
| 章 | `# 第五章　宿舍管理` | 标题文本匹配 `^第.+章` | 作为 `chapter` 元数据；一个文件只能有一个 |
| 节（可选） | `## 第一节　入住与退宿` | 标题文本匹配 `^第.+节` | 作为 `section` 元数据 |
| 条 | `### 第一条` | 标题文本匹配 `^第.+条` | 每个条一条记录 |
| 其他标题 | `（一）……`、`## 常见问答` | 不匹配上述三者 | 条内归入正文；条外记 warning |

- 条号必须独立成标题行（`### 第二十五条`）；解析器兼容“条号 + 标题同行”的历史写法，`article_title` 取条号后的文本，可为空。
- 一个文件只能有一个章标题；缺章标题时取 front matter 的 `chapter`，仍缺失则报错停止。
- 文档级 front matter 至少包含 `doc_title`、`category`，或由 `manifest.csv` 提供，合并后仍缺则报错。可选 `doc_num`、`source_url`、`chapter`。
- 章末 `## 常见问答` 下的 `### 问：…` 不是“条”，入库时记 warning 并忽略；需要进入检索的问答请改成 `### 第N条` 形式。
- 图片行 `![说明](路径)` 会原样存进条款正文，不会被解析成图片。
- 按“条”切分：一个条标题到下一条、下一节或下一章之间为一个记录，一条记录只含一条条款；条内其他标题留在所属条正文里，不单独成条。
- 正文保留原文；禁止改写、总结、补全或把整章塞成一条。
- 没有条标题的文件，只有 front matter 显式提供 `article_num` 时才允许作为单条入库；否则报错停止。
- `id` 由 `doc_num`（缺省用文件 slug）+ `chapter` + `article_num` 稳定生成；重复入库按 `id` upsert，不产生重复记录。
- `manifest.csv` 必须覆盖目录下每个 `.md` 且不重复；缺失、多余、空正文、缺 `article_num` 都直接失败，不静默跳过。条外无法识别的标题打印 warning，并在 `--dry-run` 汇总，不中断。
- embedding 文本 = `doc_title + chapter + article_title + content`；维度默认 `1024`，建 HNSW cosine 索引；换模型必须重建向量列与索引。

### 入库命令

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

## 检索

`app/services/rag_service.py` 做混合检索，不接 LLM：

- **向量路**：按 `topK * 2` 取余弦最近邻（pgvector HNSW 索引）。
- **关键词路**：问题按 `[，。！？?,!\s]+` 切词，长度 ≥ 2、去停用词（`app/resources/stopwords.yml`）、去重、最多 8 个；每个关键词对 `content` 做 `ILIKE '%kw%'`，每个词最多 5 条。
- **融合**：只命中关键词的条目记 `0.35`；同时命中向量与关键词的记 `min(0.95, 向量分 + 0.08)`；只命中向量的保留原分。按融合分降序取 `topK`。

`RETRIEVAL_MIN_SCORE`（拒答阈值）与 embedding 模型强相关：不同模型的相似度分布不可比，**换模型必须用真实问题重新标定**，不要沿用旧值。真实手册入库后也需要重新校准。

已知限制：按标点切词时，自然问句（如「宿舍能用电磁炉吗」）常只切出一个长 token，`ILIKE` 基本命中不到；关键词路主要在问题里出现条款原词（如「大功率电器」）时才起作用。切分集合是 `[，。！？?,!\s]+`，**顿号 `、` 不在其中**，用顿号分隔的词会被当成一个整体。

## 通知采集

只抓 `NOTICE_FETCH_BASE_URL` 对应的公开「通知公告」栏目，不登录、不绕鉴权、不使用浏览器自动化。`.env.example` 默认指向南阳师范学院官网；解析规则按该站页面结构编写，换站点需要修改解析规则。站点在 2026-10 检查时没有 `robots.txt` 限制声明，正式采集前仍应确认站点条款。

```bash
python scripts/fetch_notices.py --once --dry-run --pages 1   # 只解析：不写库、不算向量
python scripts/fetch_notices.py --once --pages 1             # 抓取并入库
```

- 去重：先按 `url` 跳过已入库条目的详情请求；写入时按 `content_hash`（正文去空白后 SHA-256）唯一约束 `ON CONFLICT DO NOTHING`，同一内容换个链接重发也不会重复。
- embedding 文本 = `标题 + 正文`；维度与 `vector(1024)` 一致。
- 定时采集由 APScheduler 驱动（`NOTICE_FETCH_CRON`，UTC），未配置 `NOTICE_FETCH_BASE_URL` 时不启动。**多 worker 部署会每个进程各跑一份**，见 [docs/踩坑记录.md](docs/踩坑记录.md)。
- 详情请求之间限速 0.3 秒；抓不到正文的通知记 warning 并跳过，不写空记录。
- 页面结构、翻页规则和选择器细节见 [docs/踩坑记录.md](docs/踩坑记录.md)。

## 危机告警

`crisis` 分支触发钉钉机器人（加签模式），文案在 `app/resources/alert-template.yml`。

- **加签**：`base64(HMAC-SHA256(secret, "timestamp\nsecret"))` 再做 URL 编码，拼到 webhook 的 `timestamp` / `sign` 参数上。日志与异常信息里不打印 webhook 和 secret。
- **成功判定**：钉钉即使限流或签名错也返回 HTTP 200，必须看响应体的 `errcode == 0`；`errcode != 0` 记为失败。
- **去重**：同一 `openid` 在 `ALERT_DEDUP_MINUTES` 窗口内只告警一次。判断依据是 `qa_log` 里该 openid 近期是否有 `is_high_risk=true 且 alert_sent=true` 的记录——用数据库而不是进程内字典，多 worker 下也成立。`/api/ask` 不带 openid 时不做去重。
- **`alert_sent` 语义**：发送成功 `true`；被去重抑制或发送失败 `false`。
- **先发告警、再写日志**：`qa_log` 按设计只允许 INSERT，所以 `alert_sent` 必须在写日志前确定，不能事后 UPDATE。代价是危机回复会等一次告警请求（超时 5 秒），失败不影响学生收到回复。
- **未配置 webhook 时**：危机分支照常回复，只记一条 warning，`alert_sent=false`。

告警测试接口是 `POST /admin/api/alert/test`，**必须带 Basic 认证**——它会真的往授权群发消息，不能对公网裸奔。它不参与去重、不写 `qa_log`。

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
| Embedding | 任意 OpenAI 兼容服务；默认 SiliconFlow `BAAI/bge-m3`（1024 维） |
| 微信 | `wechatpy` |
| 采集 | `httpx` + `beautifulsoup4` + `lxml` |
| 定时 | APScheduler |
| 管理后台密码 | `passlib[bcrypt]` |
| 测试 | pytest + pytest-asyncio + httpx |

## 架构简图

```text
微信用户
   │  文本 / 关注 / 菜单事件（明文或 AES 安全模式）
   ▼
GET|POST /wechat/callback
   │  文本消息立即返回空串，后台异步处理
   ▼
FastAPI ──► 规则链（QaService）
   │          0. 空问题
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

规则链优先级固定为：**危机关键词 > emergency > urgency=high > 负面情绪(venting/consult) > RAG（通知/知识库）> 留空板块 > 低相关度拒答**。危机判断之前不得调用 LLM。检索排在留空板块之前，否则「库内没有条款但官网发了通知」的问题会被判成空板块。

## 目录结构

```text
.
├── app/
│   ├── main.py            # FastAPI 实例、lifespan、路由注册、定时调度
│   ├── config.py          # Settings（pydantic-settings）
│   ├── database.py        # engine、SessionLocal、get_db
│   ├── models/            # knowledge_base / official_notice / qa_log
│   ├── schemas/           # Pydantic 模型（对外 camelCase）
│   ├── repositories/      # SQL 封装
│   ├── services/          # 规则链、RAG、LLM、微信、告警、采集
│   ├── routers/           # qa / wechat / admin
│   ├── resources/         # Prompt、词表、固定话术（YAML）
│   └── static/admin/      # 管理后台单页
├── scripts/
│   ├── ingest_knowledge.py # 知识库入库（支持 --dry-run）
│   └── fetch_notices.py    # 官网通知采集（支持 --dry-run）
├── data/
│   └── knowledge/
│       └── examples/      # 可提交的样例模板（*.example.md + manifest.example.csv）
├── alembic/
│   └── versions/          # 迁移脚本
├── tests/
│   ├── cases/rule_chain.yml
│   └── test_*.py
├── docs/
│   ├── 交接文档.md
│   ├── 接口契约.md
│   ├── 数据库.md
│   └── 踩坑记录.md
├── AGENTS.md              # AI 编码代理工作守则
├── README.md
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .dockerignore
├── alembic.ini
├── pytest.ini
├── requirements.txt
├── requirements-dev.txt
└── 开发步骤提示词.md       # 分步执行手册
```

真实知识库、`.env`、日志和测试产物只在本地保留，不提交 Git。

## 测试

```bash
pytest -q
ruff check . && ruff format --check .
```

- 集成测试**必须**连 PostgreSQL + pgvector：`tests/conftest.py` 会自动创建 `counselor_bot_test`（可用 `TEST_DB_NAME` 覆盖）并对它跑 `alembic upgrade head`，每个用例前清空三张表。**数据库不可用时明确 skip，不静默通过**；不要用 SQLite 替代 PostgreSQL。
- 单测 mock 外部 LLM / embedding / 微信 / 钉钉；不调用真实付费 API，也不打真实 webhook。
- 规则链用例在 `tests/cases/rule_chain.yml`：新增分支必须在那里写期望 `path`，`tests/test_rule_chain.py` 会逐条驱动执行。
- 当前覆盖：知识库切分（两种标题层级、稳定 id、manifest 与空正文报错）、关键词切分与融合分、规则链全分支（含「通知优先于空板块」的回归用例）、camelCase 契约、`qa_log` 只读、微信签名与 AES 往返、告警加签与去重。
- 测试不会写入 `data/knowledge/`，也不提交真实日志。

### 测试数据规则

- 真实知识库放 `data/knowledge/`；`.gitignore` 忽略其内容，但保留 examples 模板。忽略规则以 `.gitignore` 为准。
- 合成测试小样本放 `tests/fixtures/mini/`；微信真实报文脱敏后放 `tests/fixtures/wechat/`，真实 openid 不提交。
- 学生原话、openid、日志、数据库导出、`*.log`、`.env` 一律不提交 Git；测试数据、样例知识库、真实日志只放本地。

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

## 已知限制与路线图

**上线前必须处理**

- 微信真实报文联调（明文 + 安全模式）；钉钉真实送达联调，确认授权群。
- 多 worker 部署的三个问题：定时采集重复、后台登录锁定失效、微信长连接/内存状态不共享。先固定单 worker，或改用共享存储。
- 用真实《学生手册》重新入库并标定 `RETRIEVAL_MIN_SCORE`；当前阈值只在样例条款上标定过，不能直接沿用。
- 补充 `app/resources/` 中的危机词表、排除词表和敏感词表；当前是占位内容。敏感词策略目前只记录、不拦截，仍需产品确认。
- 补齐限流/防刷、微信回调幂等、CI/CD、数据库定时备份；确认管理后台与告警测试接口的访问范围和额外限制。

**可以后补**

- 通知命中时，`sources[]` 没有 url 字段可放（契约缺口，建议加 `sourceType` / `docUrl`）。
- `qa_log.need_type` 列已建但取值未定义，当前写 `null`。
- `intent_normalize_failed` 在 `path` 枚举里但实际不会返回（归一化失败即降级继续）。

## 贡献

- 动手前阅读 [AGENTS.md](AGENTS.md)：范围红线、分层职责、规则链与测试底线都在里面。
- 修改行为或接口时，同步更新 README、`docs/接口契约.md`、`docs/数据库.md`、`.env.example`。
- 提交前运行 `pytest -q` 和 `ruff check . && ruff format --check .`。
- 规则链新增分支必须写进 `tests/cases/rule_chain.yml`，并补上期望 `path`。
- 不要提交 `.env`、密钥、学生个人信息、真实日志或真实知识库。

## 许可证

仓库目前没有 `LICENSE` 文件。对外发布或接受外部贡献前，需要先确定许可证。

## 相关文档

- [docs/交接文档.md](docs/交接文档.md)：接手必读——现状总览（文件职责、接口、数据流、常数、改动指引），随代码实时维护。
- [AGENTS.md](AGENTS.md)：AI 编码代理工作守则，含范围红线、分层职责、规则链与测试底线。
- [docs/接口契约.md](docs/接口契约.md)：规则链顺序、`path` 枚举、响应字段、后台分页与 `qa_log` 写入契约。
- [docs/数据库.md](docs/数据库.md)：表结构、索引与迁移规则。
- [docs/踩坑记录.md](docs/踩坑记录.md)：环境问题、实现决策、已知规格差异，以及通知采集的页面结构细节。
- [开发步骤提示词.md](开发步骤提示词.md)：分步实施顺序与每步验收标准（根目录执行版）。
- [.env.example](.env.example)：完整环境变量模板。
