# dc_agent 后端架构参考（严格 8 层单向 DAG + 千级脚本规模化引擎）

## 1. 核心设计哲学：代码隐身，业务显形

1. **物理实现严格封存**：SQL / 存储过程代码经 `sqlglot` AST 提取与大模型受控翻译后，仅以脱敏业务事实形态落入 `logic_evidences`；物理表名、字段英文名、脚本路径与行号全部隔离在 `internal_locator_json` 中，仅作内部血缘计算与审计溯源，**绝不向资产视图或 Copilot 输出透出一词**。
2. **双门禁事前裁决（`Bus`）**：所有面向业务资产合成与问答输出的 LLM 工具调用参数与文本均经 `de_jargon_adjudicator`（拦截 SQL 关键字、物理标识符、小写蛇形变量及工程术语）和 `evidence_backing_adjudicator`（核对实证/资产编号白名单），违规即自动打回重述。
3. **强制披露与实事求是（`disclose`）**：当制度条款在系统逻辑中尚未实现（`UNREALIZED`）、多处实现存在分支/口径差异（`DIVERGENT`）或知识库无匹配（`NOT_FOUND`）时，强制通过结构化披露通道标红提醒业务人员。

---

## 2. 严格 8 层单向架构（Layer 0 ~ Layer 7）

低层模块严禁导入任何高层模块；`app/kernel/` 严禁包含任何业务词或上层导入；`app/api/` 四大路由子域严禁交叉导入。上述红线由 [test_architecture_isolation.py](file:///Users/hang/code/dc_agent/backend/tests/test_architecture_isolation.py) 在每次 CI/回归时做 AST 静态扫描强校验。

| 层级 | 包路径 | 关键模块与核心职责 |
| :--- | :--- | :--- |
| **Layer 0** | `app/infra/` | 基础设施层：`db.py`（SQLite WAL / MySQL 连接池与幂等列迁移）、`db_tables.py`（13 张 ORM 核心表）、`blob_store.py`（内容寻址 `LocalBlobStore`）、`config.py`（配置与两级预算）、`errors.py`（`WorkflowError` / `ProviderAuthOrBillingError`）、`task_runner.py`（有界线程池）、`taxonomy.py`（金融十大主题 × 二级子专题）、`retrieval.py`（多监管体系识别、N-gram + Okapi BM25 与万级 `CachedBM25Index`）、`llm/`（`RealDesignLlm`、`TokenBucketRateLimiter`、跨线程 429 冷却、`call_llm_validated` 守卫与 10 个 Tool Schema） |
| **Layer 1** | `app/kernel/` | ProdAgent 微内核（零上层/业务导入）：`bus.py`（`Bus` 事前裁决、SSE 事后广播、父子预算原子级联与回滚）、`event_log.py`（哈希链账本 `EventModel` 与状态折叠）、`run.py`（`RunContext`、`BudgetGuard`、`CancellationToken`、`submit_run` / `cancel_run` / `recover_interrupted_runs`）、`plan.py`、`scheduler.py`、`interrupt.py` |
| **Layer 2** | `app/policy/` | 策略配置层：`dc_bus.py`（`de_jargon_adjudicator`、`evidence_backing_adjudicator`、`sanitize_business_text`、`build_run_bus`、`build_script_child_bus`）、`dc_plan.py`（批任务与资产合成状态机 Reducer）、`dc_channels.py` |
| **Layer 3** | `app/ingestion/` | 双源接入与静默解析层：`project_scanner.py`（Phase 0 零模型扫描、归一化 AST 模板指纹聚簇、跨脚本读写表 DAG 与三层分流）、`code_parser.py`（SQL/PLSQL/动态 SQL 切块，支持 `full` / `lite` / `static_only`）、`ast_extractor.py`（`sqlglot` AST 逻辑细节抽取、Plan-then-Extract、脱敏 `upstream_context` 注入）、`doc_parser.py`（PDF/DOCX/XLSX/MD 双阶段制度条款提炼）、`builtin_pack.py` |
| **Layer 4** | `app/catalog/` | 业务资产合成与批协调层：`batch_ingest.py`（ZIP 安全解压/目录扫描、四波次异步流式调度、主线程逐脚本 SQLite Checkpoint、断点续跑与重启自愈）、`synthesizer.py`（克隆折叠/结构去重/Tier 2 隔离、跨源域对齐、双维分批提炼、`target_domains` 增量合成）、`views.py`（纯业务视图与缓存版本号签名）、`mutations.py`（CAS 乐观锁修改与人工确认 + 缓存失效） |
| **Layer 5** | `app/copilot/` | 智能问答层：`business_tools.py`（7 个受控纯业务工具 + 跨业务域均衡轮转）、`disclosure.py`（结构化披露器）、`answer_engine.py`（`plan_copilot_tools` → 工具召回 → `compose_business_answer` → 门禁校验与会话持久化） |
| **Layer 6** | `app/api/` | 极薄路由层（严禁跨子域互导）：`platform/system.py`、`ingestion/sources.py`、`catalog/assets.py`、`copilot/chat.py` |
| **Layer 7** | `app/main.py` | FastAPI 应用装配、CORS 与 `lifespan`（启动自动调用 `init_db()` 与 `recover_stale_batch_jobs()`） |

---

## 3. 数据库 13 张核心 ORM 表（`app/infra/db_tables.py`）

1. **内核账本与系统设置（3 张）**：`runs`、`events`、`system_settings`（持久化模型配置与执行预算）。
2. **双源接入、异步批任务与隐身实证（4 张）**：`ingestion_jobs`、`source_materials`、`regulatory_clauses`、`logic_evidences`。
3. **纯业务资产与问答台账（6 张）**：`business_capabilities`、`business_rule_assets`、`business_caliber_assets`、`asset_revisions`、`copilot_sessions`、`copilot_messages`。

历史运行库升级通过 `app/infra/db.py` 中的 `ensure_schema_columns()` 自动执行幂等 `ALTER TABLE`，不会破坏存量资产。

---

## 4. 千级 SQL 脚本规模化架构（Phase 0 → 异步 Checkpoint 引擎）

面对 1,000+ SQL / 存储过程的大型银行监管项目，系统通过四重机制消除「预算墙」「超时」「重跑」「中间域噪音」与「语义断层」问题：

### 4.1 Phase 0 零模型静态预扫描与三层分流（`project_scanner.py`）
- **多核静态画像**：并行复用 `split_sql_chunks` 与 `extract_sql_ast_skeleton`，抽出标准化 `target_tables`、`source_tables`、条件/分支/聚合/窗口复杂度、中文注释字数与监管体系标识（实测 1,000 脚本约 1.8 秒）。
- **归一化 AST 模板指纹（`ast_template_hash`）**：剥离注释并折叠常量与分行/年度分区后缀，将多机构/多期同构脚本自动聚簇为 `1 Master + N TEMPLATE_CLONE`。
- **血缘 DAG 与 Kahn 拓扑排序**：构建跨脚本「写表 → 读表」有向图并计算无死循环全序（遇环按入度与业务密度自动断环）。
- **三层分流策略**：
  - `TIER_3_SKIP_AI`：历史备份文件、纯 DDL/日志运维脚本、无加工条件的 ODS 原样透传、`TEMPLATE_CLONE` 同构副本、无核心报送下游依赖的孤立中间脚本（`ORPHAN_INTERMEDIATE`）；
  - `TIER_1_CORE`：命中监管体系/报送终表前缀或 DAG 终端核心业务脚本；
  - `TIER_2_UPSTREAM`：在血缘 DAG 中 1–3 跳内直接喂养 Tier 1 且具备实质过滤/口径分支的上游脚本。
  - 所有分级在 `READY_REVIEW` 态均支持前端/API 人工覆盖（`override_material_tier`）。

### 4.2 四波次流式调度、单写线程 Checkpoint 与故障自愈（`batch_ingest.py`）
- **Wave 0 / 0.5 / 1 / 2 / 3 严格拓扑推进**：
  - `Wave 0` 立即归档非克隆类 Tier 3 为 `SKIPPED_AI`；
  - `Wave 0.5` 解析同批制度文档；
  - `Wave 1` 对 `TIER_2_UPSTREAM` 采用 `execution_mode="lite"`（跳过规划仅调 1 次翻译，成本减半），提炼纯中文脱敏「上游业务摘要」；
  - `Wave 2` 对 `TIER_1_CORE` 采用完整 Plan-then-Extract，将 1–2 跳上游摘要注入 `translate_logic_evidence` 的 `upstream_business_context`（严守**绝不传入 `plan_code_structure`** 的测试边界），补全端到端业务口径；
  - `Wave 3` 为所有已就绪同构副本零模型克隆 Master 实证并标记 `is_template_clone = True`。
- **Single-Writer 线程模型**：Worker 线程池（受滑动窗口 `max_workers * 2` 限制）完全不触碰 SQLAlchemy Session；主协调线程通过 `wait(..., FIRST_COMPLETED)` 逐一回收结果、落库并 `session.commit()`，消除 SQLite 并发写锁竞争，并节流推送 `batch.progress` SSE 事件。
- **两级预算隔离与账单熔断**：
  - 子总线 `script_bus` 超过 `max_calls_per_script` 时，通过 `Bus` 父子级联自动回滚父预占计数，**仅将当前异常脚本标为 `FAILED`**，其余脚本继续执行；
  - 父总线 `job_bus` 触达总预算上限或遇到上游 `HTTP 401/402/403`（`ProviderAuthOrBillingError`）时，立即停止派发新任务并收敛为 `PAUSED_BUDGET`；
  - 进程意外重启后 `main.py` lifespan 调用 `recover_stale_batch_jobs()` 重置遗留 `PARSING` 状态；用户点击续跑时从 SQLite 重建上游摘要并跳过全部已完成脚本，**0 重复调用**。

---

## 5. Stage 3 增量合成、双维分类与万级 BM25 检索缓存

1. **实证收敛与降噪（`synthesizer.py`）**：
   - 已被下游 Tier 1 吸收的 Tier 2 中间实证默认仅留存 `logic_evidences` 表供审计追溯，不独立生成顶层 `BusinessCapabilityModel`，避免业务版图被数百个中间过渡域污染；
   - `_deduplicate_domain_evidences` 合并同构副本实证与重复结构指纹，同时将全部实证 ID 汇总计入 `logic_backing_count`；
   - 支持 `target_domains` 参数只重算受影响域并保留未变更域及已人工确认（`CONFIRMED`）资产。
2. **双维分类与 `CachedBM25Index`（`taxonomy.py` & `retrieval.py`）**：
   - `CachedBM25Index` 在首次检索时按 `(db_bind_id, kind, filter_sig, row_count, max_updated_at)` 预构建倒排表、IDF 与拼接小写文本，后续查询仅遍历命中 `q_unique` 或同体系的稀疏候选集（10,000 条规则典型查询 ~13.6ms）；
   - `synthesizer.py` 与 `mutations.py` 在每次事务提交后立即调用 `invalidate_bm25_caches()` 保证强一致性。

---

## 6. 前端四域工作台约束（`frontend/src/`）

由 [frontend_audit.test.mjs](file:///Users/hang/code/dc_agent/frontend/tests/frontend_audit.test.mjs) 守护：
1. `src/features/` 严格保持 4 个目录对称：`ingestion/`（含 [SourcesPage.jsx](file:///Users/hang/code/dc_agent/frontend/src/features/ingestion/SourcesPage.jsx) 与 [BatchJobPanel.jsx](file:///Users/hang/code/dc_agent/frontend/src/features/ingestion/BatchJobPanel.jsx)）、`catalog/`、`copilot/`、`platform/`；
2. 全部 HTTP 请求严格收敛于 `src/api/`（`api/client.js`、`ingestion.js`、`catalog.js`、`copilot.js`、`platform.js`），组件内严禁出现直接 `fetch(`；
3. `src/utils/labels.js` 中的所有界面映射标签严禁包含任何 SQL/表名/技术实现黑话。

---

## 7. 自动化测试矩阵（`backend/tests/` 共 39 项）

- [test_architecture_isolation.py](file:///Users/hang/code/dc_agent/backend/tests/test_architecture_isolation.py)：8 层目录、单向 DAG 依赖、`kernel` 零业务引用、API 路由防交叉导入
- [test_project_scanner_and_dag.py](file:///Users/hang/code/dc_agent/backend/tests/test_project_scanner_and_dag.py)：Phase 0 备份/DDL/日志/ODS/孤立中间表跳过、30 份同构副本聚簇、血缘环路断环、`lite` 模式与 `upstream_context` 无表名透传
- [test_batch_ingest_checkpoint_resume.py](file:///Users/hang/code/dc_agent/backend/tests/test_batch_ingest_checkpoint_resume.py)：逐脚本 Checkpoint、预算暂停与重启后 0 重复续跑、单脚本超限隔离、Zip Slip 恶意压缩包拦截、HTTP 402 余额暂停与批量 API 端到端
- [test_rate_limiter_and_two_level_budget.py](file:///Users/hang/code/dc_agent/backend/tests/test_rate_limiter_and_two_level_budget.py)：RPM 令牌桶、跨线程 429 冷却与 `CancellationToken` 秒级唤醒、父子 `Bus` 预算级联回滚
- [test_cached_bm25_scale.py](file:///Users/hang/code/dc_agent/backend/tests/test_cached_bm25_scale.py)：万级金融规则打分 bit-exact 一致性、查询延迟与修改/确认缓存失效
- [test_ast_generalization_and_llm_config.py](file:///Users/hang/code/dc_agent/backend/tests/test_ast_generalization_and_llm_config.py)：非 SAC 通用 SQL AST 泛化与密钥脱敏配置
- [test_ai_full_pipeline.py](file:///Users/hang/code/dc_agent/backend/tests/test_ai_full_pipeline.py)：全四阶段 AI 流水线与 `plan_code_structure` 物理信息隔离红线
- [test_dual_ingestion_and_synthesis.py](file:///Users/hang/code/dc_agent/backend/tests/test_dual_ingestion_and_synthesis.py)：双源解析、四大资产生成、CAS 乐观锁与 FastAPI 端到端
- [test_multi_regime_and_bm25.py](file:///Users/hang/code/dc_agent/backend/tests/test_multi_regime_and_bm25.py)：多监管体系（1104/EAST/SAFE/PLUS/一表通）与双维分类矩阵
- [test_business_copilot_and_disclosure.py](file:///Users/hang/code/dc_agent/backend/tests/test_business_copilot_and_disclosure.py) & [test_real_sac_corpus.py](file:///Users/hang/code/dc_agent/backend/tests/test_real_sac_corpus.py) & [test_stress_and_concurrency.py](file:///Users/hang/code/dc_agent/backend/tests/test_stress_and_concurrency.py)：7 大业务工具、`disclose` 强制披露、25 份真实 SAC 脚本语料与多线程并发 CAS 压测
