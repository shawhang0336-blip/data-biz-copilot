# Data Biz Copilot（`data-biz-copilot`）

> **统一 Data Agent 产品家族**：[`data-rule-studio`](https://github.com/shawhang0336-blip/data-rule-studio)（规则装配与验证） · [`data-biz-copilot`](https://github.com/shawhang0336-blip/data-biz-copilot)（业务资产与制度对账） · [`data-sql-guardian`](https://github.com/shawhang0336-blip/data-sql-guardian)（SQL 优化与发布门禁）

**语法隐身、逻辑显形** 的企业级监管与业务逻辑资产提取、制度对账与智能问答工作台。

`Data Biz Copilot` 面向银行及金融机构监管报送（`1104` / `EAST 5.0` / `SAFE` / `PLUS` / `一表通` / `人行统计`）与复杂数仓工程场景：一边读入监管/业务制度文档，一边在后台静默解析成百上千份 SQL / 存储过程逻辑；物理表名、字段名、SQL 语法与脚本路径严格隔离在底层实证索引中，对外只呈现 **业务人员可读的纯中文业务资产** 与 **按真实执行顺序串联的通俗加工流程**，并受 `Bus` 去技术化与实证白名单双重门禁硬性保护。

---

## 产品界面与双空间布局

### 1. 前台：业务问答工作台（`/`、`/copilot`）
- **检索式首页与连续文档型问答区**：默认进入干净的业务提问台（无聊天气泡），左侧提供可收起的「最近查询」历史栏与底部「平台管理」入口，首页提供常见业务示例快捷提问。
- **实时思考过程与整秒计时**：通过 `POST /api/copilot/ask/stream`（SSE）实时呈现「规划核查路径 → 业务工具检索（含关键词与命中数）→ 交叉核验依据 → 梳理结论」各步进展，计时以整秒显示；回答完成后保留可折叠的过程卡片。
- **逐字流式 Markdown 解答**：支持上游大模型逐 token 实时输出，AI 结合问题性质自主判断是否展开通俗加工步骤，支持 Markdown 分支对照表、基于已有条件的通俗举例、分歧与未落地提醒以及延伸追问列表。
- **右侧参考依据抽屉**：点击回答下方编号依据标签，右侧滑出展示系统运行实证数、按真实执行先后顺序分组的「加工流程一 / 二」（含阶段角色及纳入、剔除、分支、计算、去重细节）以及对应制度原文条款。

### 2. 后台：银行治理管理台（`/admin/*`）
深色左侧导航栏搭配浅/深色可切换工作区，顶栏集成后端在线健康探测胶囊、深浅外观切换及一键返回前台业务问答：
- **知识材料（`/admin/sources`）**：顶部 4 格统计条与可折叠三模式导入区（即时上传 / ZIP 与文件夹批量工程 / 服务端本地目录扫描），支持 `PDF / DOCX / DOC / XLSX / XLS / Markdown / TXT` 制度文档与多方言 SQL 脚本，集成大批量分流与断点续跑控制台。
- **业务资产（`/admin/assets`）**：顶部统一接入 **商业银行两级监管切换条（一级监管机构 `NFRA / PBOC / SAFE / OTHER` → 二级监管报表 `一表通 / EAST / 1104 / PLUS / 人行统计 / SAFE`）**，与金融十大主题双维交叉筛选，提供 4 个标签页（业务版图、处理规则、指标口径、制度对照）、分页数据表、右侧实证加工步骤抽屉及人工释义编辑弹窗。
- **变更与对账（`/admin/versions`）**：聚合 `asset_revisions` 呈现人工释义修订前后 Diff 与业务确认记录，集中跟进 `DIVERGENT`（口径分歧）与 `UNREALIZED`（尚未系统包含）项并支持一键 CAS 确权，同时支持导出 **整改评审报告（`.md`）** 与标准监管编码映射的 **Data Rule Studio 整改交接包（`.json`）**。
- **审计中心（`/admin/audit`）**：独立全链路合规审计台，实时重算全部批摄入与资产合成账本的 SHA-256 哈希链，提供 4 视图核查工作台（逐条哈希链防篡改验签、波次与节点耗时瀑布、去技术化防泄露 + 实证白名单双门禁拦截清单、端到端加密指纹及 `.json` 审计证据包导出）。
- **系统配置（`/admin/settings`）**：大模型服务端点与运行模式配置，以及并发频率限制与任务额度保护设置。

---

## 核心能力亮点

1. **五段式全链路流水线（Stage 0 → Stage 4）**
   - **Stage 0 零模型静态预扫描**：千级 SQL 工程 ~2 秒完成 `sqlglot` AST 解析、模板指纹聚簇与跨脚本「写表 → 读表」血缘 DAG 建图，自动三层分流砍掉 50%–70% 无效 AI 调用；
   - **Stage 1 制度文档双阶段提炼**：支持 `PDF` / `DOCX` / `XLSX` / `Markdown` / `TXT`，先做章节全局规划剔除前言废话，再分块提取结构化制度条款；
   - **Stage 2 拓扑波次异步解析与断点续跑**：按上游到下游拓扑顺序流式并发解析，每完成 1 个脚本即在主线程落盘一次 SQLite Checkpoint，用直白口语按「输入 → 保留/剔除条件 → 分支处理 → 去重输出」还原逻辑，支持两级预算保护、全局 RPM 限流、429 冷却退避、401/402/403 账单安全暂停与断点零重复续跑；
   - **Stage 3 双源碰撞对账与双维合成**：代码实证与监管条款按同体系精准对齐，产出四大纯业务资产卡（业务版图 / 处理规则 / 指标口径 / 制度对账），同时组织「垂直二级业务子专题 `sub_domain` × 水平金融十大主题 `subject_theme`」双维分类视图；
   - **Stage 4 纯业务智能问答**：通过 7 个受控业务检索工具与万级规则内存倒排 BM25 缓存（`CachedBM25Index`）毫秒级召回，按真实执行顺序串联关联实证的通俗加工步骤，强制披露分歧（`DIVERGENT`）与未落地（`UNREALIZED`）事项，严禁模型编造或泄露任何底层技术词。
2. **严格架构与安全边界**
   - 后端严守 **8 层单向 DAG 依赖红线**（`infra -> kernel -> policy -> ingestion -> catalog -> copilot -> api -> main`），`kernel` 微内核零上层业务依赖，由 AST 自动化单测门禁守护；
   - 前端严守 **4 业务域对称结构**（`catalog` / `copilot` / `ingestion` / `platform`），所有 HTTP 调用严格隔离在 `src/api/`；
   - 压缩包批量上传内置 **Zip Slip 路径穿越**、符号链接与体积/数量上限防护。

---

## 端到端流程图（Stage 0 ~ Stage 4 与 7 个 AI 介入点）

```mermaid
flowchart TD
    IN["接入入口: 即时上传 / ZIP 工程包 / 文件夹 / 本地目录扫描"] --> STG["Blob 内容寻址落盘 + SHA-256 幂等去重 + 登记批任务"]
    STG --> S0["Stage 0: 零 LLM 静态预扫描与跨脚本血缘 DAG (1000 脚本 ~2s)"]

    S0 --> T3["Tier 3 免模型跳过 (50%-70%): 备份 / DDL / 日志 / ODS 透传 / 孤立过程 / 同构副本"]
    S0 --> T2["Tier 2 上游关键加工 (Wave 1): lite 单次调用 AI #3 translate_logic_evidence"]
    S0 --> T1["Tier 1 核心报送终表 (Wave 2): AI #4 plan_code_structure -> AI #5 translate_logic_evidence"]
    S0 --> DOC["Stage 1 制度文档: AI #1 plan_document_structure -> AI #2 extract_regulatory_clauses"]

    T2 -->|"纯中文脱敏上游业务摘要 (1-2 跳)"| T1
    T3 -->|"同构模板零模型克隆继承主实证 (Wave 3)"| CK["逐脚本 SQLite Checkpoint + SSE 实时进度 / 断点续跑"]
    T1 --> CK
    DOC --> CK

    CK --> S3["Stage 3 碰撞合成: 克隆折叠 + 跨源对齐 + AI #6 synthesize_domain_assets (双维分类)"]
    S3 --> IDX["生成四大纯业务资产卡 + 自动刷新 CachedBM25Index 万级倒排缓存"]
    IDX --> S4["Stage 4 流式问答: AI #7-A plan_copilot_tools -> 7大工具与有序加工流程 -> AI #7-B 流式通俗解答"]
```

### 7 个受控大模型介入点总览

| 编号 | 阶段 | Tool 契约名称 | AI 职责 | 核心输入与隔离边界 |
| :--- | :--- | :--- | :--- | :--- |
| **AI #1** | Stage 1 文档 | `plan_document_structure` | 识别文档类型与规范域名，划分章节角色（`RULE_CLAUSE / CALIBER_DEF / CAPABILITY_SCOPE / IGNORE`） | 仅看章节标题与摘要骨架 |
| **AI #2** | Stage 1 文档 | `extract_regulatory_clauses` | 按组提炼纯业务规范条款、适用角色与关联指标 | `document_plan` + 非 `IGNORE` 章节原文 |
| **AI #3** | Stage 2 上游 | `translate_logic_evidence`（`lite`） | 对 Tier 2 上游关键加工脚本单次直译，产出纯中文脱敏「上游业务摘要」 | 内存 `sql_logic_context`（不落对外表） |
| **AI #4** | Stage 2 核心 | `plan_code_structure` | 对多步 Tier 1 脚本划分 1–4 个逻辑阶段（`SCOPE_FILTER / BRANCH_LOGIC / CALCULATION / DEDUP_MERGE / OUTPUT_REPORT / IGNORE`） | **仅纯中文注释与无表名步骤 DAG**（严禁传入原始 SQL/表名/上游上下文） |
| **AI #5** | Stage 2 核心 | `translate_logic_evidence`（`full`） | 按通俗短句逐阶段精准还原纳入范围、剔除条件、计算算式、分支与防重粒度 | 内存 `sql_logic_context` + 脱敏 `upstream_business_context` |
| **AI #6** | Stage 3 合成 | `synthesize_domain_assets` | 融合制度条款与代码实证，润色规则/口径并校准 `sub_domain`（2–6 个/域）与 `subject_theme`（十大主题） | 分批规则草案、条款、实证及 `allowed_evidence_ids` 白名单 |
| **AI #7** | Stage 4 问答 | `plan_copilot_tools` + 流式作答 / `compose_business_answer` | 先规划检索工具与横切维度，再结合按序加工步骤、召回资产与强制披露项流式生成通俗纯业务解答 | 仅可访问业务资产、脱敏加工步骤与 `allowed_asset_codes` 白名单 |

> **关键确定性控制面（0 AI 调用）**：Phase 0 静态切块与指纹聚簇、Kahn 血缘拓扑与三层分流、滑动窗口调度与逐脚本 Checkpoint、父子两级预算级联回滚、全局 RPM 令牌桶与 429 冷却、401/402/403 余额不足安全暂停、事前去技术化门禁（`de_jargon_adjudicator`）与实证白名单门禁（`evidence_backing_adjudicator`）、`CachedBM25Index` 倒排检索。

---

## 双维业务分类体系

- **垂直条线分类**：`监管体系（1104 / EAST / SAFE / PLUS / 一表通 / 人行统计）` → `核心业务域` → `二级业务子专题 sub_domain`（单域约束 2–6 个凝聚专题）
- **水平跨报表金融十大主题（`subject_theme`）**：`当事人与客户`、`协议与账户`、`交易与事件`、`财务与余额`、`授信与押品`、`机构与组织`、`产品与业务`、`渠道与媒介`、`地域与跨境`、`风控与合规管控`

更多设计细节与层间边界见 [backend/ARCHITECTURE.md](backend/ARCHITECTURE.md)。

---

## 目录结构

```text
data-biz-copilot/
├── backend/
│   ├── ARCHITECTURE.md               # 后端八层架构与千级脚本引擎参考
│   ├── app/
│   │   ├── main.py                   # Layer 7: FastAPI 入口与生命周期管理
│   │   ├── infra/                    # Layer 0: DB ORM、BlobStore、BM25 缓存、受控/流式 LLM 客户端
│   │   ├── kernel/                   # Layer 1: ProdAgent 微内核（Bus / EventLog / Run / Plan / Scheduler / 内存态 RunContext）
│   │   ├── policy/                   # Layer 2: Bus 双门禁、4 套静态 WorkflowPlan 拓扑、Channel 契约与事件折叠器
│   │   ├── ingestion/                # Layer 3: Phase 0 扫描、AST/文档解析与通俗逻辑提取
│   │   ├── catalog/                  # Layer 4: 批协调调度、双源合成、有序加工流程提炼与 CAS 编辑
│   │   ├── copilot/                  # Layer 5: 7 大受控业务工具、强制披露与流式/兜底问答引擎
│   │   └── api/                      # Layer 6: 4 域薄路由（platform 含 audit_service / ingestion / catalog / copilot）
│   └── tests/                        # 48 项离线全量测试与 AST 架构隔离守卫
├── frontend/
│   ├── src/
│   │   ├── app/                      # App.jsx（ThemeProvider + React.lazy 路由装配）、AdminLayout.jsx（5 项治理侧边栏 + 健康胶囊/主题切换）
│   │   ├── api/                      # 统一 API 客户端与流式 SSE 封装（client / copilot / catalog / ingestion / platform）
│   │   ├── components/               # 通用 UI 底座（SelectDropdown / CopyButton / BackendHealthPill）
│   │   ├── context/                  # 全局状态（ThemeContext 浅/深色外观持久化）
│   │   ├── features/
│   │   │   ├── copilot/              # 前台业务问答台（CopilotPage.jsx）与参考依据抽屉（EvidenceDrawer.jsx）
│   │   │   ├── ingestion/            # 知识材料导入与批任务控制台（SourcesPage.jsx / BatchJobPanel.jsx）
│   │   │   ├── catalog/              # 业务资产台账（AssetsPage.jsx）与变更对账整改中心（VersionsPage.jsx）
│   │   │   └── platform/             # 全链路合规审计中心（AuditCenterPage.jsx）与系统配置（SettingsPage.jsx）
│   │   ├── styles/index.css          # 全站浅/深色视觉体系、银行两级监管切换栏与治理工作台样式
│   │   └── utils/                    # 银行监管字典（bankingTaxonomy.js）、UTC 时间解析（dateTime.js）、业务词表（labels.js）、Markdown 表格渲染（markdown.jsx）与图标（icons.jsx）
│   └── tests/frontend_audit.test.mjs # 5 项前端目录对称、单向层依赖、fetch 隔离、银行分类字典与纯业务词表审计测试
└── README.md
```

---

## 核心 API 接口一览

| 分组 | 方法与路径 | 说明 |
| :--- | :--- | :--- |
| **系统与全链路审计** | `GET /api/health`、`GET /api/events/stream`、`GET /api/runs/audit-overview`、`GET /api/runs/{run_id}/audit-detail`、`GET /api/runs/{run_id}` | 健康检查、SSE 实时进度流、全量 SHA-256 哈希链实时验签、双门禁拦截明细、波次耗时瀑布与合规指纹档案 |
| **模型与预算治理** | `GET/PUT /api/settings/llm-config`、`GET/PUT /api/settings/execution-budget` | 模型配置持久化（密钥脱敏）、并发数/RPM/单脚本调用上限/批总预算 |
| **双源接入与千级批处理** | `GET /api/sources`、`POST /api/sources/upload`、`POST /api/sources/load-sample`、`POST /api/sources/load-sac-real` | 单文件接入与演示/真实 SAC 语料一键载入 |
| **大批量工程控制台** | `POST /api/sources/batch-upload`、`POST /api/sources/import-dir` | 多文件/ZIP 安全解压或本地代码仓目录扫描，返回 Phase 0 三层预览 |
| **批任务控制与调级** | `GET /api/sources/jobs`、`GET /api/sources/jobs/{job_id}`、`PATCH /api/sources/jobs/{job_id}/materials/{material_id}/tier`、`POST /api/sources/jobs/{job_id}/{start\|cancel\|resume}` | 分页查看三层明细、手工覆盖分层、启动/中止/断点续跑/仅重试失败项 |
| **资产合成、变更与跨产品交接** | `POST /api/sources/synthesize`、`GET /api/assets/panorama`、`GET /api/assets/{capabilities\|rules\|calibers\|reconcile\|revisions}`、`GET /api/assets/rule-studio-export`、`PATCH/POST /api/assets/{rules\|calibers}/{id}/confirm`、`GET /api/assets/backing/{asset_code}` | 支持全量或增量合成、商业银行一级机构/二级报表/十大主题立体过滤、修订动态流、CAS 乐观锁人工确认及 Rule Studio 整改条款交接导出 |
| **智能问答 Copilot** | `GET /api/copilot/tools`、`POST /api/copilot/ask`、`POST /api/copilot/ask/stream`、`GET /api/copilot/sessions`、`GET /api/copilot/sessions/{session_id}` | 7 个受控业务工具、同步与 SSE 实时思考/流式问答、多轮会话列表与核验追溯 |

---

## 快速启动与验证

### 1. 环境配置
复制示例配置文件按需修改（默认 `llm_provider=scripted`，**无需真实 API Key 即可完整运行离线确定性引擎**；如需启用真实大模型，填入 OpenAI 兼容端点与密钥即可，`.env` 已被 `.gitignore` 排除）：

```bash
cp .env.example .env
```

### 2. 启动后端（推荐 Python 3.9+）
复用同级 `data-rule-studio/.venv` 或使用本地虚拟环境：

```bash
cd backend
../../data-rule-studio/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
```

### 3. 启动前端工作台

```bash
cd frontend
npm install
npm run dev
```

访问浏览器默认地址（`http://127.0.0.1:5174`）即可直接使用 **前台业务问答工作台**，并通过左下角或右上角「平台管理」进入 `/admin/sources`、`/admin/assets`、`/admin/settings`。

### 4. 运行离线全量测试与架构红线检查（0 外部模型调用）

```bash
# 后端全量测试（48 项：8 层单向 DAG 隔离 + 双层 Scheduler/Plan/Channel + 全链路防篡改审计中心 + 变更流与 Rule Studio 交接 + Phase 0 分流 + 断点续跑 + 限流级联 + 万级 BM25 + 有序加工流程 + SSE 流式问答 + 真实 SAC 语料 + 高并发 CAS）
cd backend
../../data-rule-studio/.venv/bin/pytest -v -W error::sqlalchemy.exc.SAWarning

# 前端规范审计（四域对称、fetch 严格隔离在 src/api/、纯业务标签无代码黑话）与生产构建
cd ../frontend
npm test && npm run build
```
