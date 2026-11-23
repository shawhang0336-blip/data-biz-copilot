# dc_agent 后端架构导览（单向七层 DAG + AST 红线）

## 设计哲学：代码隐身，业务显形
- **代码只作地下实证依据（`LogicEvidenceModel`）**：通过确定性 AST 解析从 SQL / 存储过程提取纳入、剔除、分支、防重与计算形态，物理表名、字段名与脚本行号严格封存在 `internal_locator_json`。
- **代码 × 监管材料 → 合成纯业务资产层**：自动产出业务版图卡、业务处理规则卡、业务指标口径卡、制度 vs 逻辑对账台账。
- **对外零代码黑话门禁（`de_jargon_adjudicator`）**：一切对业务展示的资产卡与 Copilot 回答均受策略层 `Bus` 门禁强制拦截校验，严禁泄露 SQL、表名或字段名。

## 严格单向层级拓扑（低层绝不依赖高层）

| 层级 | 包路径 | 职责边界 |
|---|---|---|
| Layer 0 | `app/infra/` | 基础设施、ORM 11 张表、BlobStore、受控 LLM 客户端与守卫 |
| Layer 1 | `app/kernel/` | ProdAgent 六大核心微内核（零任何上层业务引用） |
| Layer 2 | `app/policy/` | `dc_bus`（去技术化门禁 + 实证白名单门禁）与 `dc_plan` / `dc_channels` |
| Layer 3 | `app/ingestion/` | 双源静默解析（监管制度文档解析 + SQL 代码实证提炼 + 内置样例包） |
| Layer 4 | `app/catalog/` | 双源碰撞合成引擎、纯业务资产视图、CAS 乐观锁人工确认 |
| Layer 5 | `app/copilot/` | 7 个中文业务工具、`disclose` 防胡编强制披露闸门、业务问答编排器 |
| Layer 6 | `app/api/` | 四大业务域（`platform` / `ingestion` / `catalog` / `copilot`）薄路由 |
| Layer 7 | `app/main.py` | FastAPI 应用装配与生命周期管理 |
