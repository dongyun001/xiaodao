# AGENTS.md — 辅导员 AI 问答机器人（Python）

> AI 编码代理工作守则。动手前读完；与其他文档冲突时以本文件为准，并在 `docs/踩坑记录.md` 记录。
> 技术栈、目录结构、知识库格式、环境变量见 `README.md`；规则链、`path` 枚举与字段契约见 `docs/接口契约.md`；坑与决策见 `docs/踩坑记录.md`。

## 1. 范围与红线

面向高校学生的辅导员 AI 问答服务：基于新知识库和学校官网公开通知，按固定规则链回答、标注出处、全程留痕。

- 做：`POST /api/ask`、微信回调、管理后台只读查询、官网通知采集、危机告警。
- 不做：登录学校系统、抓取需登录页面、存储个人凭据；持续对话式心理咨询/诊断/治疗/用药建议（负面情绪只做 1~2 句共情 + 转介）；多轮上下文、语音识别、转人工、办事流程指引（未经确认不加入）。
- 不编造条款、通知、联系方式、热线；检索不到就拒答。
- 只采集官网公开通知；禁止 Playwright/Selenium/pyppeteer，JS 动态页抓不到就停下报告，不自行换方案。
- 依赖写 `requirements.txt`，开发依赖写 `requirements-dev.txt`；不引入未确认的重型依赖（LangChain、Playwright、Selenium、Celery、Redis 等）。
- 不复制来源不明的钩子、部署脚本和任何凭据；`.env` 权限 600 且已 gitignore。
- 学生 openid、原话、告警内容属个人信息：仅授权人员可查；后台必须 HTTPS，建议限校园网/VPN；导出/截图需审批。
- `/admin/**` 必须 Basic 认证 + 登录失败锁定；日志查询不得支持任意 SQL。
- 外部调用必须设超时；LLM/微信/钉钉失败只降级，不把堆栈或供应商响应透给学生。

## 2. 分层职责

`models` 只放 ORM；`schemas` 只放 Pydantic；`repositories` 只封装 SQL；`services` 放业务；`routers` 只做校验/鉴权/调用/响应。禁止在路由写 SQL；跨层重复逻辑抽到 service/repository。

## 3. 必须守住的不变量

- **规则链严格按序执行，命中即返回，不得跳步；危机关键词永远最先判断，且该分支不得调用 LLM。** 完整顺序、通知分支条件与 `path` 枚举见 `docs/接口契约.md`；危机判断不得交给模型。
- 每个分支返回统一 `QaResult`，不在分支里拼 HTTP 响应；异常在边界捕获并降级为稳定话术。
- 响应字段必须 camelCase（`docTitle`、`topSimilarity`、`generatedAnswer`、`createdAt`、`alertSent`）；Pydantic v2 用 `alias_generator=to_camel`、`populate_by_name=True`；改字段先改契约文档和前端，再改后端。
- 知识库按“条”切分，一条记录只含一条条款；条的位置按标题文本（`第X章`/`第X节`/`第X条`）识别，**不限定 `##` 或 `###`**。`id` 稳定 upsert；空正文、无法确定 `article_num`、元数据缺失直接报错，不静默跳过。规则见 `README.md`「知识库准备」；标题约定在 `README.md`、`docs/踩坑记录.md`、`开发步骤提示词.md` 三处必须一致。
- 向量维度、embedding 模型与 `vector(N)` 必须一致；换模型必须重建向量列与 HNSW 索引。
- 配置只从环境变量读，唯一入口 `app/config.py`；Prompt/词表/固定话术放 `app/resources/*.yml`，原样保留、不写进 Python。缺必填变量时启动失败并报变量名，不打印值。已知配置坑（`DEEPSEEK_BASE_URL` 带 `/v1`、`PT60S` 转秒、微信变量命名）见 `docs/踩坑记录.md`。
- `qa_log` 只允许 INSERT 和只读查询；禁止 UPDATE/DELETE 接口、后台按钮或 ORM 写方法。写入细节见 `docs/接口契约.md`。
- 新增配置同步 `.env.example` + README；禁止在代码、测试、文档、compose 中硬编码真实密钥、密码、webhook、token、手机号。

## 4. 常用命令

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows（Git Bash）
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8081
pytest -q
ruff check . && ruff format --check .
alembic upgrade head
alembic revision --autogenerate -m "add xxx"
python scripts/ingest_knowledge.py --input data/knowledge
python scripts/fetch_notices.py --once
docker compose up -d --build
curl http://localhost:8081/api/health   # 期望 OK
```
- 入库/采集脚本必须支持 `--dry-run`；端口统一 8081，不使用 8080。

## 5. 编码规范

- 公共函数/方法写类型注解；不用 `Any` 兜底，确需使用写明原因。
- Pydantic v2 用 `model_validate`、`model_dump(by_alias=True)`；禁止 `.dict()`、`.json()`、`parse_obj`。
- SQLAlchemy 2.0 用 `DeclarativeBase`、`Mapped`、`mapped_column`、`select()`；禁止 legacy `Query` 和字符串拼接 SQL。
- 数据库统一同步 Session：在 `def` 端点或 `run_in_threadpool` 调用；async 端点禁止直接阻塞查询。外部 HTTP 用 `httpx.AsyncClient`。
- Prompt 渲染用 `.replace("{user_input}", ...)`，不用 `str.format`（Prompt 含 JSON 花括号）。
- 时间统一 UTC 存储、按配置时区展示；禁止 naive datetime；用 `logging`，不要 `print`。
- 日志不得包含学生原话全文。

## 6. 测试底线

- pytest + pytest-asyncio；接口测试用 httpx.AsyncClient/TestClient。
- 集成测试必须连 PostgreSQL + pgvector；**禁止 SQLite 替代，禁止 mock 掉向量检索**；数据库不可用时失败或明确跳过，不静默通过。
- 单测 mock 外部 LLM/embedding/微信/钉钉；禁止调用真实付费 API 或真实 webhook。
- 新增/修改行为必须新增测试；覆盖规则链优先级、camelCase 契约、知识库切分、低相关度拒答、`qa_log` 只读、微信签名、告警去重。
- 规则链用例维护在 `tests/cases/rule_chain.yml`，新增分支必须写期望 `path`；提交前 `pytest -q` 与 `ruff check .` 通过。
- 每个开发步骤只做一次相关验证（一次测试或一次 dry-run），不要每次编辑后重复入库、调真实接口或微信回调。
- 真实知识库放 `data/knowledge/`；学生原话、openid、日志、`.env` 一律不提交 Git。排除规则以 `.gitignore` 为准（`data/knowledge/*` + 反向排除 examples），不得写成整目录忽略。

## 7. Git 与提交

- 禁止提交 `.env`、密钥、证书、数据库导出、日志、知识库大文件、临时产物、`.venv/`、`__pycache__/`；向量数据不入库。
- commit message 用 `type(scope): 摘要`，正文写“为什么”；禁止 `update`、`fix bug`；一个提交只做一件事。
- 提交前看 `git diff --cached`；首次推送前跑 `git grep -nE '(API_KEY|SECRET|PASSWORD|WEBHOOK|TOKEN)='`。
- 不对 `main` 强推或改写已推送历史。

## 8. 文档同步

- 改代码必须同步更新文档，禁止“先代码后补”。
- 行为/接口 → `README.md` + `docs/接口契约.md`；环境变量 → `.env.example` + README；表结构 → `docs/数据库.md`（首次 Alembic 迁移时创建）+ Alembic；坑与决策 → `docs/踩坑记录.md`。
- 文档写实际行为、命令、字段名；禁止写未实现规划或用空改动糊弄；冲突时同一次提交内一起改。

## 9. 不确定就停下

- 需求含糊、规格冲突，或涉及新增功能/依赖/数据源/改 API 字段时，先列选项和影响，等确认。
- 不自行扩大范围（企业微信、EMERGENCY 菜单、多轮对话、限流方案等未确认功能）。
- 不为“跑通”而改规则链、降阈值、跳过测试或 mock 数据库。

## 10. 提交前自检

- [ ] 规则链顺序未改（危机分支不调用 LLM），响应 camelCase 且 `path` 在枚举内
- [ ] 新配置已进 `.env.example`，无硬编码密钥
- [ ] 知识库按“条”入库，元数据完整
- [ ] `qa_log` 无写/删接口
- [ ] 新增行为有测试，pytest + ruff 通过
- [ ] README/docs 已同步，暂存区无 `.env`/临时产物
- [ ] 已知规格差异未被“顺手修复”，见 `docs/踩坑记录.md` 的「已知规格差异与当前取舍」
